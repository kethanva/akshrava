package org.akshrava.app

import android.os.SystemClock
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

data class Quality(val maxSide: Int = 640, val jpegQ: Int = 55, val fps: Double = 1.0) {
    /** Prefer the cheaper capture of the two (lower side / JPEG q / FPS). */
    fun moreConservative(other: Quality) = Quality(
        maxSide = minOf(maxSide, other.maxSide),
        jpegQ = minOf(jpegQ, other.jpegQ),
        fps = minOf(fps, other.fps)
    )

    companion object {
        /**
         * Server guidance is advisory; never let a malformed response raise phone cost.
         * Floor matches FrameEncoder's usable JPEG range so 3G ladders can request Q28–Q32.
         */
        fun fromServer(maxSide: Int, jpegQ: Int, fps: Double) = Quality(
            maxSide = maxSide.coerceIn(320, 640),
            jpegQ = jpegQ.coerceIn(25, 70),
            fps = fps.coerceIn(0.2, 2.0)
        )
    }
}

/** Bounded, non-sensitive result telemetry for UI diagnostics and live E2E assertions. */
data class DetectionTelemetry(
    val frameId: Long,
    val detectionCount: Int,
    val labels: List<String>,
    val lateSuppressed: Boolean,
    val resultAgeMs: Long
)

class ProtocolClient(
    private val endpoint: String,
    private val token: String,
    private val alertManager: AlertManager,
    private val onState: (String) -> Unit,
    private val onFrameSettled: () -> Unit,
    private val onQuality: (Quality) -> Unit,
    private val onHighAlert: () -> Unit = {},
    /** Observed header→result latency for link-adaptive capture (AssistService / tests). */
    private val onRoundTripMs: (Long) -> Unit = {},
    /** Fired when an in-flight frame hits the settle deadline (before optional reconnect). */
    private val onSettleTimeout: () -> Unit = {},
    private val onResultTelemetry: (DetectionTelemetry) -> Unit = {},
    private val onTerminal: (String) -> Unit = {},
    private val language: String = "en",
    private val http: OkHttpClient = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build(),
    private val debugTelemetry: Boolean = false
) : WebSocketListener() {
    internal companion object {
        const val MAX_BACKOFF_ATTEMPT = 4          // 2^4 = 16 s, capped to 10 s
        const val MAX_BACKOFF_SECONDS = 10.0
        /**
         * End-to-end phone freshness budget: age = elapsedRealtime() - capture_mono_ms.
         *
         * A hazard older than this is never spoken — the user has already walked past it. Keep
         * this tight; it is a safety boundary, not a tuning knob.
         *
         * Measured against the live remote deployment with realistic 640x480 q55 frames
         * (scripts/soak_session.py): RTT median 498 ms, p90 618 ms, max 752 ms, of which server
         * inference was 129-324 ms. That is roughly 4x headroom, so 2500 ms suppresses nothing
         * in practice today. An earlier note here claimed this had to cover a worst-case
         * ALERT_MAX_AGE_MS=8500 CPU path and should therefore be 9000 ms; the deployment is far
         * faster than that assumption, and widening the budget to 9 s would licence speaking
         * about an obstacle the user passed nine seconds ago. If the backend is ever moved to a
         * genuinely slow inference path, fix the latency rather than widening this.
         */
        const val STALE_ALERT_MS = 2_500L
        /** Look answers use the full freshness budget even when the hazard is S1. */
        const val LOOK_FRESHNESS_MS = 2_500L
        const val URGENT_FRESHNESS_MS = 1_500L
        /**
         * Allows a result to settle after slow inference while preserving the speak budget.
         * A late result is diagnosed but never announced.
         */
        const val FRAME_SETTLE_TIMEOUT_MS = 10_000L
        /** Look answers use the same settle budget; announce failure if unanswered. */
        const val LOOK_TIMEOUT_MS = FRAME_SETTLE_TIMEOUT_MS
        /** Soft timeouts shed quality first; only repeated hangs tear down the socket. */
        const val SETTLE_TIMEOUTS_BEFORE_RECONNECT = 2

        /** How long one *outstanding* frame may go unanswered before the stale tick starts (F-30). */
        const val STALE_INFERENCE_TICK_AFTER_MS = 3_000L
        /** Gap between stale ticks while that same frame is still unanswered. */
        const val STALE_INFERENCE_TICK_PERIOD_MS = 2_000L
        /**
         * Ticks emitted per stuck frame before the watchdog goes quiet.
         *
         * Bounded on purpose. An unbounded tick becomes a permanent beep the moment anything holds
         * the link — and a permanent beep in the user's only audio channel masks the alerts it is
         * supposed to be flagging the absence of. FRAME_SETTLE_TIMEOUT_MS already recovers the slot.
         */
        const val STALE_INFERENCE_MAX_TICKS = 3

        /**
         * True when the stale-inference earcon should fire right now (F-30).
         *
         * Deliberately keyed off an *outstanding frame* rather than "time since the last result".
         * The capture interval is adaptive and legitimately reaches 5 s (CapturePolicy MIN_FPS /
         * BATTERY_LOW_FPS = 0.2) and 2 s (thermal throttle); a wall-clock rule at 3 s therefore
         * beeps continuously on a perfectly healthy session as soon as the phone gets warm or the
         * battery gets low — precisely when the user can least afford noise. A frame that was sent
         * and never answered is the only thing that actually means "inference stalled".
         */
        fun shouldTickStaleInference(inFlight: Boolean, frameAgeMs: Long, ticksAlready: Int): Boolean =
            inFlight && frameAgeMs >= STALE_INFERENCE_TICK_AFTER_MS && ticksAlready < STALE_INFERENCE_MAX_TICKS

        /** A response may release only the exact frame that still owns the single-flight slot. */
        fun frameMaySettle(currentFrameId: Long?, responseFrameId: Long): Boolean =
            currentFrameId != null && currentFrameId == responseFrameId

        /** Device revocation is an operator action, not a network condition to retry. */
        fun isPermanentAccessClose(code: Int): Boolean = code == 4401 || code == 4403

        /** Dual-session fencing: the loser socket is closed after a JSON session_superseded body. */
        fun isSessionTakenOverClose(code: Int) = code == 4409

        /** JSON error codes that must tear down the session even if the close code is lost. */
        fun isTerminalErrorCode(code: String): Boolean = when (code) {
            "session_superseded", "device_revoked", "authentication_failed" -> true
            else -> false
        }

        fun speechKeyForTerminalError(code: String): String = when (code) {
            "session_superseded" -> "op_session_taken_over"
            "device_revoked" -> "op_access_revoked"
            else -> "op_auth_failed"
        }

        /**
         * Pre-accept Uvicorn rejects collapse to HTTP 401/403 with no JSON. 403 is revoke
         * (matching iOS); 401 is a bad token. After accept, JSON + 4401/4403 are preferred.
         */
        fun speechKeyForHandshakeHttp(status: Int): String =
            if (status == 403) "op_access_revoked" else "op_auth_failed"

        /**
         * Control-only soft sheds have no frame_id and must not free the in-flight JPEG slot.
         * A corrupt ping that called settleFrame() made the real result arrive as a late miss.
         */
        fun shouldSettleUnownedSoftError(code: String): Boolean =
            code != "malformed_control_message" && code != "unknown_message"

        val FORBIDDEN_AWARENESS_PREFIXES = listOf("saf", "clear", "cross", "navigat", "collis", "approach")

        fun awarenessTextIsSpeakable(text: String): Boolean {
            if (text.isBlank()) return false
            val lower = text.lowercase()
            return FORBIDDEN_AWARENESS_PREFIXES.none { it in lower }
        }

        const val SOFT_SHED_ANNOUNCE_AFTER = 3
        const val SOFT_SHED_ANNOUNCE_COOLDOWN_MS = 15_000L

        fun shouldAnnounceSoftShed(
            consecutiveSoftSheds: Int,
            lastAnnounceAtMonoMs: Long,
            nowMonoMs: Long
        ): Boolean {
            if (consecutiveSoftSheds < SOFT_SHED_ANNOUNCE_AFTER) return false
            if (lastAnnounceAtMonoMs == 0L) return true
            return nowMonoMs - lastAnnounceAtMonoMs >= SOFT_SHED_ANNOUNCE_COOLDOWN_MS
        }

        /** Wire contract uses en|hi; AppConfig stores BCP-47 tags like en-IN / hi-IN. */
        fun wireLanguage(tag: String): String = SupportedLanguages.wireCode(tag)

        /** Keeps the stream gate independently testable: a transport-only socket is not vision. */
        fun streamEnabled(sessionReady: Boolean, visionEnabled: Boolean): Boolean =
            sessionReady && visionEnabled

        /** Server policy may tighten speech freshness, but can never widen the phone-owned cap. */
        fun configuredSpeakBudget(serverAdvertisedMs: Long): Long =
            serverAdvertisedMs.coerceIn(0L, STALE_ALERT_MS)

        fun maxSpeakAgeMs(priority: Boolean, isUrgent: Boolean, configuredMs: Long): Long = when {
            priority -> minOf(LOOK_FRESHNESS_MS, configuredMs)
            isUrgent -> minOf(URGENT_FRESHNESS_MS, configuredMs)
            else -> configuredMs
        }

        /**
         * Soft server rejects: free the in-flight slot and keep the socket. These are framing /
         * admission / overload conditions, not a dead vision vendor.
         */
        fun isSoftServerError(code: String): Boolean = when (code) {
            "worker_saturated",
            "frame_in_flight",
            "frame_rate_limited",
            "non_monotonic_capture",
            "invalid_image_size",
            "invalid_jpeg",
            "jpeg_dimension_mismatch",
            "unsupported_frame_size",
            "invalid_frame_header",
            "unknown_message",
            "malformed_control_message" -> true
            else -> false
        }

        /** Sustained breaker state: keep the socket for recovery probes, but announce once. */
        fun isInferenceOutageError(code: String): Boolean = code == "inference_circuit_open"

        /**
         * Pose is centidegrees of device pitch/roll. The wire allows ±180°; extreme values only
         * invalidate geometry server-side — they must never tear down the session.
         */
        const val POSE_CDEG_MIN = -18_000
        const val POSE_CDEG_MAX = 18_000
        /**
         * Older server revisions treat pose < -9000 as a fatal ProtocolError and close the
         * WebSocket (the unavailable↔restored flap the user hears as the app dying and coming
         * back). Against such a revision, only emit pose values that old floor accepts. Geometry
         * already treats |roll| > 12° as invalid, so omitting these extremes changes no alert.
         *
         * This clamp is now conditional on what the server actually advertises rather than
         * unconditional. That is the whole point: previously the phone had no way to know which
         * revision it was talking to, so the workaround could never be removed -- it would have
         * outlived the bug by years, silently discarding valid pose data forever. Once no
         * deployment without [CAPABILITY_POSE_CDEG_FULL_RANGE] remains, this constant and
         * [wirePoseCdeg]'s parameter can be deleted with evidence instead of hope.
         */
        const val LEGACY_POSE_CDEG_FLOOR = -9_000

        /** Server advertises it accepts the documented ±18000 pose range. */
        const val CAPABILITY_POSE_CDEG_FULL_RANGE = "pose_cdeg_full_range"
        /** Server accepts the optional, post-result acknowledgement control message. */
        const val CAPABILITY_RESULT_ACKNOWLEDGEMENT = "result_acknowledgement"

        fun clampPoseCdeg(value: Int): Int = value.coerceIn(POSE_CDEG_MIN, POSE_CDEG_MAX)

        /**
         * Null means "omit this field from the frame header".
         *
         * [serverAcceptsFullPoseRange] defaults to false so an unknown or older server -- and any
         * caller that has not negotiated yet -- keeps the safe legacy behaviour. Failing closed
         * here costs a little pose fidelity; failing open costs the session.
         */
        fun wirePoseCdeg(value: Int, serverAcceptsFullPoseRange: Boolean = false): Int? {
            val clamped = clampPoseCdeg(value)
            if (serverAcceptsFullPoseRange) return clamped
            return if (clamped < LEGACY_POSE_CDEG_FLOOR) null else clamped
        }

        /**
         * Read the `capabilities` array out of a `ready` payload.
         *
         * Tolerant by design: a malformed or absent array yields an empty set, which selects the
         * conservative legacy behaviour everywhere. A capability list is an optimisation hint,
         * so it must never be a reason to fail a connection a blind user is depending on.
         */
        fun parseCapabilities(payload: JSONObject): Set<String> {
            val array = payload.optJSONArray("capabilities") ?: return emptySet()
            val found = mutableSetOf<String>()
            for (index in 0 until array.length()) {
                val item = array.optString(index, "")
                if (item.isNotBlank()) found.add(item)
            }
            return found
        }

        /** Sanitized class for operator logs; neither endpoint nor server body is retained. */
        fun transportFailureClass(httpStatus: Int?): String = when (httpStatus) {
            401, 403 -> "authentication"
            null -> "transport"
            else -> "http"
        }

        /** Stable, reason-free class for close diagnostics. */
        fun closeClass(code: Int): String = when (code) {
            1000 -> "normal"
            1001 -> "peer_going_away"
            1011 -> "server_error"
            1013 -> "temporary_overload"
            4401, 4403 -> "authentication"
            4409 -> "session_conflict"
            else -> "other"
        }

        /** App-level ping keeps the Redis admission lease warm when capture is briefly quiet. */
        const val APP_PING_INTERVAL_MS = 60_000L
    }
    private val reconnect: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor()
    private val earcons = ConnectionEarcons()
    // Exactly one frame is in flight at a time. The server advertises max_in_flight in its ready
    // payload; that value is logged for diagnostics but deliberately not stored as client state,
    // because nothing here honours a value above 1 and a field suggesting otherwise reads as a
    // capability the client does not have.
    private val frameSlotLock = Any()
    private var inFlightFrameId: Long? = null
    @Volatile private var socket: WebSocket? = null
    private var pendingReconnect: ScheduledFuture<*>? = null
    private val connectionGeneration = AtomicInteger(0)
    @Volatile private var closedByUser = false
    @Volatile private var outageAnnounced = false
    @Volatile private var inferenceOutageAnnounced = false
    @Volatile private var hasEverVisionReady = false
    @Volatile private var sessionReady = false
    @Volatile private var visionEnabled = false

    /**
     * What the currently-connected server revision says it supports.
     *
     * Connection-scoped and re-read from every `ready`: a reconnect can land on a different
     * Cloud Run revision (including an older one during a rollout or rollback), so capabilities
     * must never outlive the socket that advertised them. Empty = negotiate nothing, assume the
     * oldest supported behaviour.
     */
    @Volatile private var serverCapabilities: Set<String> = emptySet()
    @Volatile private var serverProtocolVersion = 0
    @Volatile private var reconnectAttempt = 0
    @Volatile private var cloudFallbackWarningAnnounced = false
    private val settleTimeoutLock = Any()
    @Volatile private var pendingSettleTimeout: ScheduledFuture<*>? = null
    @Volatile private var pendingSettleFrameId: Long? = null
    @Volatile private var pendingAppPing: ScheduledFuture<*>? = null
    @Volatile private var frameSentAtMonoMs = 0L
    @Volatile private var consecutiveSettleTimeouts = 0
    @Volatile private var consecutiveSoftSheds = 0
    @Volatile private var lastSoftShedAnnounceAtMs = 0L
    @Volatile private var connectedAtMonoMs = 0L
    /** Stale earcons already emitted for the frame currently in flight; see shouldTickStaleInference. */
    @Volatile private var staleInferenceTicks = 0
    @Volatile private var pendingStaleInferenceWatchdog: ScheduledFuture<*>? = null

    fun connect() {
        if (endpoint.isBlank() || token.isBlank()) {
            logConnection("connect_rejected", mapOf("reason" to "missing_provisioning"))
            onState("Provisioning required")
            return
        }
        closedByUser = false
        inferenceOutageAnnounced = false
        hasEverVisionReady = false
        openSocket("initial")
    }

    private fun openSocket(origin: String) {
        if (closedByUser) return
        pendingReconnect?.cancel(false)
        pendingReconnect = null
        val previous = socket
        socket = null
        previous?.cancel()
        val generation = connectionGeneration.incrementAndGet()
        logConnection(
            "connect_attempt",
            mapOf(
                "origin" to origin,
                "generation" to generation,
                "reconnectAttempt" to reconnectAttempt,
                "replacedSocket" to (previous != null)
            )
        )
        val opened = runCatching {
            http.newWebSocket(
                Request.Builder().url(endpoint).header("Authorization", "Bearer $token").build(),
                GenerationGuard(generation)
            )
        }.getOrElse {
            Log.e("AkshravaVision", "websocket_open_failed generation=$generation", it)
            if (isCurrentGeneration(generation)) handleDrop("open_failed")
            return
        }
        // Only publish if this open is still the latest generation (a racing reconnect may
        // have already bumped past us).
        if (generation == connectionGeneration.get()) {
            socket = opened
        } else {
            opened.cancel()
        }
    }

    /** True only after ready with a live detector — not transport-only noop bench mode. */
    fun canStream(): Boolean = streamEnabled(sessionReady, visionEnabled)

    /** True only when the connected revision has explicitly said it accepts the full pose range. */
    internal fun serverAcceptsFullPoseRange(): Boolean =
        CAPABILITY_POSE_CDEG_FULL_RANGE in serverCapabilities

    /** True only when this server explicitly understands the optional result acknowledgement. */
    internal fun serverAcceptsResultAcknowledgements(): Boolean =
        CAPABILITY_RESULT_ACKNOWLEDGEMENT in serverCapabilities

    /** Protocol version advertised by the connected revision; 0 means "did not say". */
    internal fun negotiatedProtocolVersion(): Int = serverProtocolVersion

    /**
     * Forget what the previous socket advertised.
     *
     * Called when a connection opens or ends, never on a soft in-session error: a reconnect may
     * land on a different revision, and inheriting the old one's capabilities would send it
     * values it closes the socket over -- reproducing the exact flap this negotiation removes.
     */
    private fun clearNegotiatedCapabilities() {
        serverCapabilities = emptySet()
        serverProtocolVersion = 0
    }

    /**
     * True once this client can never recover on its own.
     *
     * Set by handlePermanentFailure (device revoked / token rejected: close 4401/4403 or HTTP
     * 401/403) and by close(); both stop the reconnect executor, so no amount of waiting brings
     * the session back. AssistService uses this to tell a genuinely dead session apart from one
     * that is merely mid-reconnect, because pressing Start MUST still rebuild a dead session —
     * that is the only recovery the user has.
     */
    fun isTerminal(): Boolean = closedByUser

    /** Force the normal transport recovery path when the service detects an impossible slot age. */
    fun recoverFrameStall() {
        if (closedByUser) return
        logConnection("frame_slot_recovery")
        val current = socket
        if (current != null) {
            // OkHttp reports cancel through the generation-guarded failure callback, which owns
            // outage speech, exact settlement, and reconnect scheduling.
            current.cancel()
        } else {
            handleDrop("frame_slot_watchdog")
        }
    }

    fun sendFrame(
        frameId: Long,
        captureMonoMs: Long,
        pose: PoseSnapshot?,
        calibrationId: String,
        frame: EncodedFrame,
        mode: String = "normal",
        priority: Boolean = false
    ): Boolean {
        val look = priority || mode == "priority"
        val ws = socket ?: return failSendFrame(look, "socket_missing")
        // Do not produce traffic or imply an active service before the authenticated server has
        // explicitly confirmed that a real detector, rather than bench-mode NoopDetector, is live.
        if (!canStream()) return failSendFrame(look, "vision_not_ready")
        if (!tryClaimFrame(frameId)) return failSendFrame(look, "frame_in_flight")
        // From here until scheduleSettleTimeout() below, this call owns the in-flight slot and
        // nothing is yet armed to release it. An exception escaping that window -- JSON assembly,
        // or OkHttp throwing on a socket that is closing under us -- used to latch `inFlight`
        // forever. AssistService's own catch clears its separate framePending flag, so capture
        // carries on and every later frame reaches this method only to bounce off the CAS above
        // with "frame_in_flight": a session that never sends another frame again while the
        // camera, the socket and the foreground notification all still look perfectly healthy.
        // The stall detector cannot see it (analysis callbacks keep arriving) and no drop fires
        // (the socket is open), so nothing recovers and the user is never told.
        return try {
            sendFrameLocked(ws, look, frameId, captureMonoMs, pose, calibrationId, frame, mode)
        } catch (ex: Exception) {
            settleFrame(frameId)
            Log.e("AkshravaVision", "frame_send_threw id=$frameId", ex)
            AgentDebugLog.log(
                "H4",
                "ProtocolClient.sendFrame:threw",
                "frame_send_threw",
                mapOf("frameId" to frameId, "error" to (ex::class.simpleName ?: "Exception"))
            )
            failSendFrame(look, "send_threw")
        }
    }

    /** Body of [sendFrame] once the in-flight slot is held; see the caller for why it is guarded. */
    private fun sendFrameLocked(
        ws: WebSocket,
        look: Boolean,
        frameId: Long,
        captureMonoMs: Long,
        pose: PoseSnapshot?,
        calibrationId: String,
        frame: EncodedFrame,
        mode: String
    ): Boolean {
        val header = JSONObject()
            .put("type", "frame")
            .put("id", frameId)
            .put("capture_mono_ms", captureMonoMs)
            .put("capture_epoch_ms", System.currentTimeMillis())
            .put("w", frame.width)
            .put("h", frame.height)
            .put("jpeg_bytes", frame.jpeg.size)
            .put("camera_calibration_id", calibrationId)
            .put("mode", if (look) "priority" else mode)
            .put("priority", look)
            .put("language", wireLanguage(language))
            .put("trace_id", "frame-$frameId-$captureMonoMs")
            // New servers use this only to decide whether a missing acknowledgement is meaningful.
            // Older parsers ignore additive frame fields, so this never changes their behaviour.
            .put("result_acknowledgement", true)
        // Omit absent pose keys rather than sending JSON null. Values below the legacy -9000
        // floor are omitted so an undeployed/older API cannot fatal-close the walking session.
        val fullPose = serverAcceptsFullPoseRange()
        pose?.pitchCdeg?.let { raw -> wirePoseCdeg(raw, fullPose)?.let { header.put("pitch_cdeg", it) } }
        pose?.rollCdeg?.let { raw -> wirePoseCdeg(raw, fullPose)?.let { header.put("roll_cdeg", it) } }
        pose?.ageMs?.let { header.put("pose_age_ms", it.coerceAtLeast(0L)) }
        
        if (debugTelemetry) {
            header.put("debug_telemetry", true)
        }
        // Header and JPEG are a pair in the server protocol.  OkHttp queues WebSocket messages
        // independently, so if it accepts the header but rejects the JPEG we must tear down the
        // socket rather than let the next JPEG attach to this header.
        if (!ws.send(header.toString())) {
            settleFrame(frameId)
            return failSendFrame(look, "header_rejected")
        }
        if (!ws.send(frame.jpeg.toByteString())) {
            ws.close(1011, "incomplete frame")
            settleFrame(frameId)
            return failSendFrame(look, "jpeg_rejected")
        }
        if (!markFrameSent(frameId, SystemClock.elapsedRealtime())) return false
        scheduleSettleTimeout(look, frameId)
        Log.i("AkshravaVision", "frame_sent id=$frameId endpoint_class=${EndpointPolicy.classify(endpoint).logValue}")
        return true
    }

    /** Every control action must be confirmed by voice (§6.4): an explicit look that never
     * even made it onto the wire must not resolve into silence just because it wasn't sent. */
    private fun failSendFrame(isLook: Boolean, reason: String): Boolean {
        Log.i("AkshravaVision", "frame_drop reason=$reason session_ready=$sessionReady vision_enabled=$visionEnabled")
        if (isLook) {
            alertManager.announceLookFailed()
            earcons.lookFailed()
        }
        return false
    }

    private fun scheduleSettleTimeout(isLook: Boolean, frameId: Long) {
        cancelSettleTimeout()
        val future = runCatching {
            reconnect.schedule({
                // A cancelled timer may already be executing. It must never release or diagnose a
                // newer frame that acquired the slot after this frame settled.
                if (!settleFrame(frameId)) return@schedule
                if (isLook) {
                    alertManager.announceLookFailed()
                    earcons.lookFailed()
                }
                // Unblock the camera immediately, then shed capture cost. Reconnect only after
                // repeated hangs so a single slow CPU infer does not reset the WSS session.
                onSettleTimeout()
                consecutiveSettleTimeouts += 1
                if (!closedByUser && consecutiveSettleTimeouts >= SETTLE_TIMEOUTS_BEFORE_RECONNECT) {
                    consecutiveSettleTimeouts = 0
                    socket?.cancel()
                    scheduleReconnect("repeated_settle_timeout")
                }
            }, FRAME_SETTLE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        }.getOrElse {
            // Executor rejected the task (already shut down). Without this, the slot stays held
            // forever because no timeout was armed to release it — the session is silently dead.
            settleFrame(frameId)
            null
        }
        if (future != null) {
            synchronized(settleTimeoutLock) {
                if (ownsInFlightFrame(frameId)) {
                    pendingSettleFrameId = frameId
                    pendingSettleTimeout = future
                } else {
                    future.cancel(false)
                }
            }
        }
    }

    private fun cancelSettleTimeout(expectedFrameId: Long? = null) {
        val future = synchronized(settleTimeoutLock) {
            if (expectedFrameId != null && pendingSettleFrameId != expectedFrameId) {
                null
            } else {
                pendingSettleTimeout.also {
                    pendingSettleTimeout = null
                    pendingSettleFrameId = null
                }
            }
        }
        future?.cancel(false)
    }

    private fun isCurrentGeneration(generation: Int): Boolean =
        generation == connectionGeneration.get()

    override fun onOpen(webSocket: WebSocket, response: Response) {
        // Direct listener methods are unused; GenerationGuard forwards current-generation events.
    }

    override fun onMessage(webSocket: WebSocket, text: String) = Unit

    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) = Unit

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = Unit

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = Unit

    private fun handleOpen() {
        val recovered = outageAnnounced
        reconnectAttempt = 0
        sessionReady = false
        visionEnabled = false
        // A new socket has negotiated nothing yet, even if the previous one had.
        clearNegotiatedCapabilities()
        cloudFallbackWarningAnnounced = false
        consecutiveSoftSheds = 0
        lastSoftShedAnnounceAtMs = 0L
        connectedAtMonoMs = SystemClock.elapsedRealtime()
        logConnection("transport_open", mapOf("recovered" to recovered))
        Log.i("AkshravaDebug", "ws_open endpoint_class=${EndpointPolicy.classify(endpoint).logValue}")
        // Transport alone is not vision. Announcing "Connection restored" here made every
        // blip/reconnect sound recovered even when the next frame immediately failed closed again.
        onState("Transport connected; checking vision service")
    }

    private fun handleMessage(webSocket: WebSocket, text: String) {
        val payload = runCatching { JSONObject(text) }.getOrNull() ?: run {
            Log.w("AkshravaVision", "invalid_server_json")
            onState("Invalid server response")
            // The message has no frame ownership. Keep the exact frame timeout armed instead of
            // letting unrelated malformed traffic free the slot.
            return
        }
        when (payload.optString("type")) {
            "ready" -> {
                sessionReady = true
                visionEnabled = payload.optBoolean("vision_enabled", false)
                val serverMaxAge = payload.optLong("alert_max_age_ms", STALE_ALERT_MS)
                configuredStaleAlertMs = configuredSpeakBudget(serverMaxAge)
                val advertised = payload.optInt("max_in_flight", 1).coerceIn(1, 2)
                // Negotiate compatibility per connection. Re-read on every `ready` so a reconnect
                // that lands on a different (possibly older) revision renegotiates rather than
                // carrying the previous socket's assumptions into a server that cannot honour
                // them. An absent field means an old server: assume nothing.
                serverCapabilities = parseCapabilities(payload)
                serverProtocolVersion = payload.optInt("protocol_version", 0)
                Log.i(
                    "AkshravaDebug",
                    "ws_ready detector=${payload.optString("detector", "unknown")} vision_enabled=$visionEnabled " +
                        "session_ready=$sessionReady max_in_flight=$advertised alert_max_age_ms=$configuredStaleAlertMs " +
                        "protocol_version=$serverProtocolVersion capabilities=$serverCapabilities"
                )
                logConnection(
                    "vision_ready",
                    mapOf(
                        "detector" to payload.optString("detector", "unknown"),
                        "visionEnabled" to visionEnabled,
                        "maxInFlight" to advertised,
                        "alertMaxAgeMs" to configuredStaleAlertMs,
                        "protocolVersion" to serverProtocolVersion,
                        "fullPoseRange" to serverAcceptsFullPoseRange()
                    )
                )
                // #region agent log
                AgentDebugLog.log(
                    "H2",
                    "ProtocolClient.handleMessage:ready",
                    "ws_ready",
                    mapOf(
                        "visionEnabled" to visionEnabled,
                        "detector" to payload.optString("detector", "unknown"),
                        "alertMaxAgeMs" to configuredStaleAlertMs,
                        "maxInFlight" to advertised
                    )
                )
                // #endregion
                if (visionEnabled) {
                    val firstVisionReady = !hasEverVisionReady
                    hasEverVisionReady = true
                    if (outageAnnounced) {
                        outageAnnounced = false
                        // A transport handshake is not proof that inference recovered. When a
                        // circuit outage was already active, wait for an actual result before
                        // announcing restoration; otherwise ready is sufficient for a link-only
                        // outage.
                        if (!inferenceOutageAnnounced) {
                            earcons.restored()
                            alertManager.statusKey("op_restored")
                        }
                    } else if (firstVisionReady) {
                        earcons.connected()
                        alertManager.statusKey("op_connected")
                    }
                    onState("Vision assistance connected")
                    scheduleAppPing()
                    scheduleStaleInferenceWatchdog()
                } else {
                    cancelAppPing()
                    cancelStaleInferenceWatchdog()
                    val message = "Vision model unavailable. Use cane or guide."
                    onState(message)
                    if (!outageAnnounced) {
                        outageAnnounced = true
                        alertManager.statusKey("op_model_unavailable")
                    }
                }
            }
            "error" -> {
                val code = payload.optString("code")
                val errorFrameId = payload.optLong("frame_id", -1L)
                if (errorFrameId >= 0L && !ownsInFlightFrame(errorFrameId)) {
                    // A delayed error for an already-settled frame has no authority over the
                    // newer frame or its user-facing state. Processing it could announce an old
                    // outage after a fresh result had already restored the session.
                    Log.i("AkshravaVision", "late_frame_error_ignored code=$code frame=$errorFrameId")
                    return
                }
                if (isTerminalErrorCode(code)) {
                    handlePermanentFailure(speechKeyForTerminalError(code))
                    return
                }
                val settleErrorFrame = {
                    if (errorFrameId >= 0L) {
                        settleFrame(errorFrameId)
                    } else if (shouldSettleUnownedSoftError(code)) {
                        settleFrame()
                    }
                }
                when {
                    isInferenceOutageError(code) -> {
                        settleErrorFrame()
                        val message = "Vision assistance unavailable. Use cane or guide."
                        onState(message)
                        if (!inferenceOutageAnnounced) {
                            inferenceOutageAnnounced = true
                            alertManager.statusKey("op_vision_unavailable")
                        }
                    }
                    isSoftServerError(code) -> {
                        // Soft shed: keep socket, free in-flight slot, let the next frame retry.
                        Log.i("AkshravaDebug", "ws_soft_error code=$code")
                        // #region agent log
                        AgentDebugLog.log(
                            "H4",
                            "ProtocolClient.handleMessage:softError",
                            "ws_soft_error",
                            mapOf("code" to code)
                        )
                        // #endregion
                        settleErrorFrame()
                        consecutiveSoftSheds += 1
                        val nowMonoMs = SystemClock.elapsedRealtime()
                        if (shouldAnnounceSoftShed(consecutiveSoftSheds, lastSoftShedAnnounceAtMs, nowMonoMs)) {
                            lastSoftShedAnnounceAtMs = nowMonoMs
                            alertManager.statusKey("op_server_shedding")
                        }
                        onState("Server busy; shedding frames")
                    }
                    code == "vision_unavailable" -> {
                        sessionReady = false
                        visionEnabled = false
                        settleErrorFrame()
                        val message = "Vision assistance unavailable. Use cane or guide."
                        onState(message)
                        if (!outageAnnounced) {
                            outageAnnounced = true
                            alertManager.statusKey("op_vision_unavailable")
                        }
                        // The server will close this socket after the error. Closing proactively
                        // also protects older deployments that do not, and starts normal backoff.
                        socket?.close(1011, "vision unavailable")
                    }
                    else -> {
                        Log.w("AkshravaDebug", "ws_hard_error code=$code")
                        settleErrorFrame()
                        onState("Server protocol error")
                    }
                }
            }
            "pong" -> Unit
            "quality" -> onQuality(Quality.fromServer(
                payload.optInt("max_side", 640),
                payload.optInt("jpeg_q", 55),
                payload.optDouble("fps", 1.0)
            ))
            "result" -> {
                val resultFrameId = payload.optLong("frame_id", -1L)
                if (resultFrameId < 0L) {
                    // A result without ownership must not free the slot. Leave the exact
                    // frame timer armed so normal timeout recovery handles this broken response.
                    Log.w("AkshravaVision", "result_missing_frame_id")
                    onState("Invalid server response")
                    return
                }
                if (!ownsInFlightFrame(resultFrameId)) {
                    // Duplicate/late results must not speak, update outage state, acknowledge, or
                    // release a newer slot. Freshness alone is insufficient ownership evidence.
                    Log.i("AkshravaVision", "late_result_ignored frame=$resultFrameId")
                    return
                }
                // The stale-inference tick budget is reset by settleFrame() below, which is the
                // single place the in-flight slot is released.
                payload.optString("trace_id", "").takeIf { it.isNotBlank() }?.let {
                    // No device ID, endpoint, image, or location is logged; this is only a
                    // cross-tier frame correlation key for diagnosing glass-to-ear latency.
                    Log.i("AkshravaTrace", "result trace=$it")
                }
                if (payload.optBoolean("cloud_fallback_unavailable", false)) {
                    if (!cloudFallbackWarningAnnounced) {
                        cloudFallbackWarningAnnounced = true
                        val message = "Cloud vision fallback unavailable. Use cane or guide."
                        onState(message)
                        alertManager.statusKey("op_cloud_fallback_unavailable")
                    }
                } else {
                    cloudFallbackWarningAnnounced = false
                }
                val frameMono = payload.optLong("capture_mono_ms", -1)
                val age = if (frameMono >= 0) SystemClock.elapsedRealtime() - frameMono else Long.MAX_VALUE
                val priority = payload.optBoolean("priority", false)
                // Only the response for the frame that currently owns the slot contributes RTT.
                // A late response for a timed-out frame may still update diagnostics, but it must
                // never cancel the newer frame's timeout or make its RTT appear enormous.
                val sentAt = sentAtForFrame(resultFrameId)
                if (sentAt != null && sentAt > 0L) {
                    onRoundTripMs(SystemClock.elapsedRealtime() - sentAt)
                }
                val hazard = payload.optJSONObject("hazard")
                val isUrgent = hazard?.optString("level") == "urgent"
                // Look answers use the full freshness budget even if the hazard is S1 —
                // a user-pulled query must not be dropped by the tighter S1 window on slow links.
                val maxAge = maxSpeakAgeMs(priority, isUrgent, configuredStaleAlertMs)
                val lookSummary = payload.optString("look_summary", "").ifBlank {
                    hazard?.optString("spoken_preview", "") ?: ""
                }
                val hasFreshAwareness = age <= maxAge &&
                    ((priority && lookSummary.isNotBlank()) || hazard != null)
                if (inferenceOutageAnnounced && age <= maxAge) {
                    inferenceOutageAnnounced = false
                    onState("Vision assistance restored")
                    // Do not put a recovery status behind (and therefore potentially flush) an
                    // awareness utterance from this same result. When there is no fresh spoken
                    // content, say the recovery explicitly.
                    if (!hasFreshAwareness) {
                        earcons.restored()
                        alertManager.statusKey("op_restored")
                    }
                }
                val detectionCount = payload.optInt("detection_count", -1)
                val labels = payload.optJSONArray("detection_labels")
                val labelValues = buildList {
                    if (labels != null) for (i in 0 until labels.length()) add(labels.optString(i))
                }
                val lateSuppressed = payload.optBoolean("late_suppressed", false)
                Log.i(
                    "AkshravaVision",
                    "frame=$resultFrameId detections=$detectionCount labels=$labelValues " +
                        "late_suppressed=$lateSuppressed result_age_ms=$age priority=$priority"
                )
                // #region agent log
                AgentDebugLog.log(
                    "H3",
                    "ProtocolClient.handleMessage:result",
                    "ws_result",
                    mapOf(
                        "frameId" to resultFrameId,
                        "detectionCount" to detectionCount,
                        "lateSuppressed" to lateSuppressed,
                        "ageMs" to age,
                        "maxAgeMs" to maxAge,
                        "hasHazard" to (hazard != null),
                        "messageKey" to (hazard?.optString("message_key") ?: ""),
                        "speakAllowed" to (age <= maxAge),
                        "priority" to priority
                    )
                )
                // #endregion
                onResultTelemetry(
                    DetectionTelemetry(
                        frameId = resultFrameId,
                        detectionCount = detectionCount,
                        labels = labelValues,
                        lateSuppressed = lateSuppressed,
                        resultAgeMs = age
                    )
                )
                val labelHint = when {
                    labels != null && labels.length() > 0 -> {
                        buildString {
                            for (i in 0 until minOf(labels.length(), 3)) {
                                if (i > 0) append('+')
                                append(labels.optString(i))
                            }
                        }
                    }
                    detectionCount > 0 -> "${detectionCount}dets"
                    detectionCount == 0 -> "0dets"
                    else -> null
                }
                if (age <= maxAge) {
                    if (priority && lookSummary.isNotBlank()) {
                        if (awarenessTextIsSpeakable(lookSummary)) {
                            alertManager.speakComposed(lookSummary, urgent = true)
                            onState("Live · ${hazard?.optString("message_key") ?: labelHint ?: "look"}")
                        } else {
                            Log.e("AkshravaVision", "look_summary_rejected_by_safety_guard")
                            alertManager.statusKey("op_look_unavailable")
                            onState("Look summary rejected")
                        }
                    } else if (hazard != null) {
                        val isNear = hazard.optBoolean("range_valid", false) &&
                            hazard.optString("range_band") == "near"
                        if (isUrgent || isNear) {
                            onHighAlert()
                        }
                        alertManager.announce(
                            hazard.optString("message_key"),
                            hazard.optString("bearing", "ahead"),
                            isUrgent,
                            hazard.optString("haptic", "none")
                        )
                        onState("Live · ${hazard.optString("message_key")}")
                    } else if (labelHint != null) {
                        val suffix = if (lateSuppressed) " (delayed)" else ""
                        onState("Live · $labelHint$suffix")
                    }
                } else if (labelHint != null) {
                    // Still surface detector output when speech was suppressed as late.
                    onState("Live · $labelHint (delayed)")
                }
                if (serverAcceptsResultAcknowledgements()) {
                    acknowledgeResult(
                        webSocket = webSocket,
                        frameId = resultFrameId,
                        fresh = age <= maxAge
                    )
                }
                if (settleFrame(resultFrameId)) {
                    consecutiveSettleTimeouts = 0
                    consecutiveSoftSheds = 0
                }
            }
        }
    }

    /**
     * Tell the server that this specific result reached the phone and passed the phone-owned
     * freshness gate. This is deliberately best-effort and has no user-facing failure path: an
     * acknowledgement failure must never delay, suppress, or replace an awareness alert.
     */
    private fun acknowledgeResult(webSocket: WebSocket, frameId: Long, fresh: Boolean) {
        if (frameId < 0L) return
        val accepted = runCatching {
            webSocket.send(
                JSONObject()
                    .put("type", "result_ack")
                    .put("frame_id", frameId)
                    .put("fresh", fresh)
                    .toString()
            )
        }.getOrDefault(false)
        if (!accepted) {
            Log.i("AkshravaVision", "result_ack_not_sent frame=$frameId")
        }
    }

    private fun handlePermanentFailure(speechKey: String) {
        if (closedByUser) return
        settleFrame()
        cancelSettleTimeout()
        cancelAppPing()
        cancelStaleInferenceWatchdog()
        sessionReady = false
        visionEnabled = false
        clearNegotiatedCapabilities()
        inferenceOutageAnnounced = false
        closedByUser = true
        val diagnostic = AlertManager.operationalText(speechKey, "en").ifEmpty { "Session ended" }
        onState(diagnostic)
        alertManager.statusKey(speechKey)
        pendingReconnect?.cancel(false)
        reconnect.shutdownNow()
        onTerminal(speechKey)
    }

    private fun handleDrop(cause: String) {
        settleFrame()
        cancelSettleTimeout()
        cancelAppPing()
        cancelStaleInferenceWatchdog()
        val wasReady = sessionReady
        val wasVisionEnabled = visionEnabled
        // #region agent log
        Log.i("AkshravaDebug", "ws_drop sessionReady=$sessionReady visionEnabled=$visionEnabled")
        AgentDebugLog.log(
            "H2",
            "ProtocolClient.handleDrop",
            "ws_drop",
            mapOf("sessionReady" to sessionReady, "visionEnabled" to visionEnabled, "closedByUser" to closedByUser)
        )
        // #endregion
        sessionReady = false
        visionEnabled = false
        clearNegotiatedCapabilities()
        logConnection(
            "transport_drop",
            mapOf(
                "cause" to cause,
                "wasReady" to wasReady,
                "wasVisionEnabled" to wasVisionEnabled,
                "connectedForMs" to connectedDurationMs()
            )
        )
        if (closedByUser) return
        earcons.dropped()
        if (!outageAnnounced) {
            outageAnnounced = true
            // No local detector is bundled. Do not imply that the phone can still see after the
            // server link is lost.
            val message = "Vision assistance unavailable. Use cane or guide."
            onState(message)
            if (!inferenceOutageAnnounced) alertManager.statusKey("op_link_lost")
        }
        scheduleReconnect(cause)
    }

    private fun scheduleAppPing() {
        cancelAppPing()
        pendingAppPing = runCatching {
            reconnect.scheduleWithFixedDelay({
                if (closedByUser || !canStream()) return@scheduleWithFixedDelay
                val ws = socket ?: return@scheduleWithFixedDelay
                // OkHttp protocol pings do not reach FastAPI; this JSON ping renews admission.
                if (!ws.send(JSONObject().put("type", "ping").toString())) {
                    Log.i("AkshravaDebug", "ws_app_ping_failed")
                    logConnection("app_ping_send_failed")
                }
            }, APP_PING_INTERVAL_MS, APP_PING_INTERVAL_MS, TimeUnit.MILLISECONDS)
        }.getOrElse {
            Log.w("AkshravaVision", "app_ping_not_armed", it)
            null
        }
    }

    private fun cancelAppPing() {
        pendingAppPing?.cancel(false)
        pendingAppPing = null
    }

    private fun scheduleStaleInferenceWatchdog() {
        cancelStaleInferenceWatchdog()
        pendingStaleInferenceWatchdog = runCatching {
            reconnect.scheduleWithFixedDelay({
                // scheduleWithFixedDelay cancels the whole repeating task the first time it
                // throws, and does so without a trace. Contain it so one bad tick cannot silently
                // retire the watchdog for the rest of the session.
                runCatching {
                    if (closedByUser || !canStream()) return@runCatching
                    claimStaleInferenceTick(SystemClock.elapsedRealtime())?.let { tick ->
                        logConnection(
                            "stale_inference_tick",
                            mapOf("frameAgeMs" to tick.frameAgeMs, "tick" to tick.number)
                        )
                        earcons.staleTick()
                    }
                }.onFailure { Log.w("AkshravaDebug", "stale_inference_tick_failed", it) }
            }, STALE_INFERENCE_TICK_AFTER_MS, STALE_INFERENCE_TICK_PERIOD_MS, TimeUnit.MILLISECONDS)
        }.getOrElse {
            // The executor is already shut down. Losing the tick is survivable (the settle timeout
            // still recovers the slot), but it must not be lost silently.
            Log.w("AkshravaDebug", "stale_inference_watchdog_not_armed", it)
            null
        }
    }

    private fun cancelStaleInferenceWatchdog() {
        pendingStaleInferenceWatchdog?.cancel(false)
        pendingStaleInferenceWatchdog = null
        synchronized(frameSlotLock) { staleInferenceTicks = 0 }
    }

    private fun scheduleReconnect(cause: String) {
        if (closedByUser) return
        pendingReconnect?.cancel(false)
        val backoffSeconds = min(MAX_BACKOFF_SECONDS, 2.0.pow(reconnectAttempt.toDouble()))
        val attempt = reconnectAttempt + 1
        reconnectAttempt = attempt.coerceAtMost(MAX_BACKOFF_ATTEMPT)
        val delayMs = ((backoffSeconds + Random.nextDouble(0.0, 0.5)) * 1000).toLong()
        logConnection(
            "reconnect_scheduled",
            mapOf("cause" to cause, "attempt" to attempt, "delayMs" to delayMs)
        )
        earcons.reconnectPending()
        pendingReconnect = runCatching {
            reconnect.schedule({
                logConnection("reconnect_executing", mapOf("attempt" to attempt))
                openSocket("reconnect")
            }, delayMs, TimeUnit.MILLISECONDS)
        }.getOrElse {
            Log.e("AkshravaVision", "reconnect_not_scheduled cause=$cause", it)
            null
        }
    }

    @Volatile private var configuredStaleAlertMs: Long = STALE_ALERT_MS

    private data class StaleInferenceTick(val frameAgeMs: Long, val number: Int)

    private fun tryClaimFrame(frameId: Long): Boolean = synchronized(frameSlotLock) {
        if (inFlightFrameId != null) {
            false
        } else {
            inFlightFrameId = frameId
            frameSentAtMonoMs = 0L
            staleInferenceTicks = 0
            true
        }
    }

    private fun ownsInFlightFrame(frameId: Long): Boolean = synchronized(frameSlotLock) {
        frameMaySettle(inFlightFrameId, frameId)
    }

    private fun markFrameSent(frameId: Long, sentAtMonoMs: Long): Boolean =
        synchronized(frameSlotLock) {
            if (!frameMaySettle(inFlightFrameId, frameId)) {
                false
            } else {
                frameSentAtMonoMs = sentAtMonoMs
                true
            }
        }

    private fun sentAtForFrame(frameId: Long): Long? = synchronized(frameSlotLock) {
        frameSentAtMonoMs.takeIf { frameMaySettle(inFlightFrameId, frameId) }
    }

    /** Atomically charges a bounded stale tick to the exact frame that is still outstanding. */
    private fun claimStaleInferenceTick(nowMonoMs: Long): StaleInferenceTick? =
        synchronized(frameSlotLock) {
            val currentFrameId = inFlightFrameId ?: return@synchronized null
            if (!frameMaySettle(inFlightFrameId, currentFrameId)) return@synchronized null
            val ageMs = if (frameSentAtMonoMs > 0L) nowMonoMs - frameSentAtMonoMs else 0L
            if (!shouldTickStaleInference(true, ageMs, staleInferenceTicks)) {
                return@synchronized null
            }
            staleInferenceTicks += 1
            StaleInferenceTick(ageMs, staleInferenceTicks)
        }

    /** Release the current slot, optionally only when [expectedFrameId] still owns it. */
    private fun settleFrame(expectedFrameId: Long? = null): Boolean {
        val releasedFrameId = synchronized(frameSlotLock) {
            val current = inFlightFrameId ?: return@synchronized null
            if (expectedFrameId != null && !frameMaySettle(current, expectedFrameId)) {
                return@synchronized null
            }
            inFlightFrameId = null
            frameSentAtMonoMs = 0L
            staleInferenceTicks = 0
            current
        } ?: return false
        cancelSettleTimeout(releasedFrameId)
        onFrameSettled()
        return true
    }

    private fun connectedDurationMs(): Long =
        connectedAtMonoMs.takeIf { it > 0L }?.let { SystemClock.elapsedRealtime() - it } ?: -1L

    /** Fields are deliberately limited to state and timing; never pass tokens, URLs, images, or IDs. */
    private fun logConnection(event: String, data: Map<String, Any?> = emptyMap()) {
        val details = data.entries.joinToString(" ") { "${it.key}=${it.value}" }
        Log.i(
            "AkshravaConnection",
            "event=$event endpoint_class=${EndpointPolicy.classify(endpoint).logValue}" +
                if (details.isBlank()) "" else " $details"
        )
        if (debugTelemetry) {
            AgentDebugLog.log("H2", "ProtocolClient.connection", event, data)
        }
    }

    fun close() {
        logConnection("client_close", mapOf("connectedForMs" to connectedDurationMs()))
        closedByUser = true
        connectionGeneration.incrementAndGet()
        pendingReconnect?.cancel(false)
        pendingReconnect = null
        cancelSettleTimeout()
        cancelAppPing()
        cancelStaleInferenceWatchdog()
        sessionReady = false
        visionEnabled = false
        clearNegotiatedCapabilities()
        socket?.close(1000, "user stopped")
        socket?.cancel()
        socket = null
        settleFrame()
        reconnect.shutdownNow()
        http.dispatcher.executorService.shutdown()
        earcons.release()
    }

    /** Forwards OkHttp callbacks only when they belong to the current connection generation. */
    private inner class GenerationGuard(private val generation: Int) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!isCurrentGeneration(generation)) return
            handleOpen()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!isCurrentGeneration(generation)) return
            handleMessage(webSocket, text)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentGeneration(generation)) return
            logConnection(
                "server_closing",
                mapOf("generation" to generation, "code" to code, "closeClass" to closeClass(code))
            )
            settleFrame()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentGeneration(generation)) return
            Log.i("AkshravaDebug", "ws_closed code=$code endpoint_class=${EndpointPolicy.classify(endpoint).logValue}")
            logConnection(
                "server_closed",
                mapOf(
                    "generation" to generation,
                    "code" to code,
                    "closeClass" to closeClass(code),
                    "connectedForMs" to connectedDurationMs()
                )
            )
            if (isPermanentAccessClose(code)) {
                val speechKey = if (code == 4403) "op_access_revoked" else "op_auth_failed"
                handlePermanentFailure(speechKey)
            } else if (isSessionTakenOverClose(code)) {
                handlePermanentFailure("op_session_taken_over")
            } else {
                handleDrop("closed_${closeClass(code)}")
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!isCurrentGeneration(generation)) return
            Log.w(
                "AkshravaDebug",
                "ws_failure endpoint_class=${EndpointPolicy.classify(endpoint).logValue} " +
                    "http_status=${response?.code ?: "none"} failure_class=${transportFailureClass(response?.code)} " +
                    "error_type=${t.javaClass.simpleName}"
            )
            logConnection(
                "transport_failure",
                mapOf(
                    "generation" to generation,
                    "httpStatus" to (response?.code ?: "none"),
                    "failureClass" to transportFailureClass(response?.code),
                    "errorType" to t.javaClass.simpleName,
                    "connectedForMs" to connectedDurationMs()
                )
            )
            if (response?.code == 401 || response?.code == 403) {
                handlePermanentFailure(ProtocolClient.speechKeyForHandshakeHttp(response.code))
            } else {
                handleDrop("failure_${transportFailureClass(response?.code)}")
            }
        }
    }
}
