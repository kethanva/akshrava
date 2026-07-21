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
import java.util.concurrent.atomic.AtomicBoolean
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

        /** Device revocation is an operator action, not a network condition to retry. */
        fun isPermanentAccessClose(code: Int): Boolean = code == 4401 || code == 4403

        /** Wire contract uses en|hi; AppConfig stores BCP-47 tags like en-IN / hi-IN. */
        fun wireLanguage(tag: String): String = SupportedLanguages.wireCode(tag)

        /** Keeps the stream gate independently testable: a transport-only socket is not vision. */
        fun streamEnabled(sessionReady: Boolean, visionEnabled: Boolean): Boolean =
            sessionReady && visionEnabled

        /**
         * Soft server rejects: free the in-flight slot and keep the socket. These are framing /
         * admission / overload conditions, not a dead vision vendor.
         */
        fun isSoftServerError(code: String): Boolean = when (code) {
            "worker_saturated",
            "frame_rate_limited",
            "non_monotonic_capture",
            "invalid_image_size",
            "invalid_jpeg",
            "jpeg_dimension_mismatch",
            "unsupported_frame_size",
            "invalid_frame_header",
            "unknown_message" -> true
            else -> false
        }

        /**
         * Pose is centidegrees of device pitch/roll. The wire allows ±180°; extreme values only
         * invalidate geometry server-side — they must never tear down the session.
         */
        const val POSE_CDEG_MIN = -18_000
        const val POSE_CDEG_MAX = 18_000
        /**
         * Live Cloud Run still runs an older parser that treats pose < -9000 as a fatal
         * ProtocolError and closes the WebSocket (the unavailable↔restored flap). Until that
         * revision is replaced, only emit pose values the old floor accepts. Geometry already
         * treats |roll| > 12° as invalid, so omitting these extremes does not change alerts.
         */
        const val LEGACY_POSE_CDEG_FLOOR = -9_000

        fun clampPoseCdeg(value: Int): Int = value.coerceIn(POSE_CDEG_MIN, POSE_CDEG_MAX)

        /** Null means "omit from the frame header" for legacy-API compatibility. */
        fun wirePoseCdeg(value: Int): Int? {
            val clamped = clampPoseCdeg(value)
            return if (clamped < LEGACY_POSE_CDEG_FLOOR) null else clamped
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
            else -> "other"
        }

        /** App-level ping keeps the Redis admission lease warm when capture is briefly quiet. */
        const val APP_PING_INTERVAL_MS = 60_000L
    }
    private val reconnect: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor()
    private val inFlight = AtomicBoolean(false)
    @Volatile private var maxInFlight: Int = 1
    private var socket: WebSocket? = null
    private var pendingReconnect: ScheduledFuture<*>? = null
    private val connectionGeneration = AtomicInteger(0)
    @Volatile private var closedByUser = false
    @Volatile private var outageAnnounced = false
    @Volatile private var sessionReady = false
    @Volatile private var visionEnabled = false
    @Volatile private var reconnectAttempt = 0
    @Volatile private var cloudFallbackWarningAnnounced = false
    @Volatile private var pendingLookTimeout: ScheduledFuture<*>? = null
    @Volatile private var pendingSettleTimeout: ScheduledFuture<*>? = null
    @Volatile private var pendingAppPing: ScheduledFuture<*>? = null
    @Volatile private var pendingLook = false
    @Volatile private var frameSentAtMonoMs = 0L
    @Volatile private var consecutiveSettleTimeouts = 0
    @Volatile private var connectedAtMonoMs = 0L

    fun connect() {
        if (endpoint.isBlank() || token.isBlank()) {
            logConnection("connect_rejected", mapOf("reason" to "missing_provisioning"))
            onState("Provisioning required")
            return
        }
        closedByUser = false
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
        val opened = http.newWebSocket(
            Request.Builder().url(endpoint).header("Authorization", "Bearer $token").build(),
            GenerationGuard(generation)
        )
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
        if (!inFlight.compareAndSet(false, true)) return failSendFrame(look, "frame_in_flight")
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
            settleFrame()
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
        // Omit absent pose keys rather than sending JSON null. Values below the legacy -9000
        // floor are omitted so an undeployed/older API cannot fatal-close the walking session.
        pose?.pitchCdeg?.let { raw -> wirePoseCdeg(raw)?.let { header.put("pitch_cdeg", it) } }
        pose?.rollCdeg?.let { raw -> wirePoseCdeg(raw)?.let { header.put("roll_cdeg", it) } }
        pose?.ageMs?.let { header.put("pose_age_ms", it.coerceAtLeast(0L)) }
        
        if (debugTelemetry) {
            header.put("debug_telemetry", true)
        }
        // Header and JPEG are a pair in the server protocol.  OkHttp queues WebSocket messages
        // independently, so if it accepts the header but rejects the JPEG we must tear down the
        // socket rather than let the next JPEG attach to this header.
        if (!ws.send(header.toString())) {
            settleFrame()
            return failSendFrame(look, "header_rejected")
        }
        if (!ws.send(frame.jpeg.toByteString())) {
            ws.close(1011, "incomplete frame")
            settleFrame()
            return failSendFrame(look, "jpeg_rejected")
        }
        frameSentAtMonoMs = SystemClock.elapsedRealtime()
        scheduleSettleTimeout(look)
        Log.i("AkshravaVision", "frame_sent id=$frameId endpoint_class=${EndpointPolicy.classify(endpoint).logValue}")
        return true
    }

    /** Every control action must be confirmed by voice (§6.4): an explicit look that never
     * even made it onto the wire must not resolve into silence just because it wasn't sent. */
    private fun failSendFrame(isLook: Boolean, reason: String): Boolean {
        Log.i("AkshravaVision", "frame_drop reason=$reason session_ready=$sessionReady vision_enabled=$visionEnabled")
        if (isLook) {
            cancelLookTimeout()
            alertManager.announceLookFailed()
        }
        return false
    }

    private fun scheduleSettleTimeout(isLook: Boolean) {
        cancelSettleTimeout()
        pendingLook = isLook
        pendingSettleTimeout = runCatching {
            reconnect.schedule({
                pendingSettleTimeout = null
                val look = pendingLook
                pendingLook = false
                if (look) alertManager.announceLookFailed()
                // Unblock the camera immediately, then shed capture cost. Reconnect only after
                // repeated hangs so a single slow CPU infer does not reset the WSS session.
                onSettleTimeout()
                settleFrame()
                consecutiveSettleTimeouts += 1
                if (!closedByUser && consecutiveSettleTimeouts >= SETTLE_TIMEOUTS_BEFORE_RECONNECT) {
                    consecutiveSettleTimeouts = 0
                    socket?.cancel()
                    scheduleReconnect("repeated_settle_timeout")
                }
            }, FRAME_SETTLE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        }.getOrElse {
            // Executor rejected the task (already shut down). Without this, inFlight stays true
            // forever because no timeout was armed to release it — the session is silently dead.
            settleFrame()
            null
        }
        if (isLook) {
            // Keep the look-timeout handle alias for cancel paths that still name it.
            pendingLookTimeout = pendingSettleTimeout
        }
    }

    private fun cancelSettleTimeout() {
        pendingSettleTimeout?.cancel(false)
        pendingSettleTimeout = null
        pendingLookTimeout = null
        pendingLook = false
    }

    private fun cancelLookTimeout() = cancelSettleTimeout()

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
        cloudFallbackWarningAnnounced = false
        connectedAtMonoMs = SystemClock.elapsedRealtime()
        logConnection("transport_open", mapOf("recovered" to recovered))
        Log.i("AkshravaDebug", "ws_open endpoint_class=${EndpointPolicy.classify(endpoint).logValue}")
        // Transport alone is not vision. Announcing "Connection restored" here made every
        // blip/reconnect sound recovered even when the next frame immediately failed closed again.
        onState("Transport connected; checking vision service")
    }

    private fun handleMessage(text: String) {
        val payload = runCatching { JSONObject(text) }.getOrNull() ?: run {
            onState("Invalid server response")
            settleFrame()
            return
        }
        when (payload.optString("type")) {
            "ready" -> {
                sessionReady = true
                visionEnabled = payload.optBoolean("vision_enabled", false)
                val serverMaxAge = payload.optLong("alert_max_age_ms", STALE_ALERT_MS)
                configuredStaleAlertMs = serverMaxAge.coerceAtLeast(STALE_ALERT_MS)
                val advertised = payload.optInt("max_in_flight", 1).coerceIn(1, 2)
                maxInFlight = advertised
                Log.i(
                    "AkshravaDebug",
                    "ws_ready detector=${payload.optString("detector", "unknown")} vision_enabled=$visionEnabled " +
                        "session_ready=$sessionReady max_in_flight=$advertised alert_max_age_ms=$configuredStaleAlertMs"
                )
                logConnection(
                    "vision_ready",
                    mapOf(
                        "detector" to payload.optString("detector", "unknown"),
                        "visionEnabled" to visionEnabled,
                        "maxInFlight" to advertised,
                        "alertMaxAgeMs" to configuredStaleAlertMs
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
                    if (outageAnnounced) {
                        outageAnnounced = false
                        alertManager.status("Connection restored")
                    }
                    onState("Vision assistance connected")
                    scheduleAppPing()
                } else {
                    cancelAppPing()
                    val message = "Vision model unavailable. Use cane or guide."
                    onState(message)
                    if (!outageAnnounced) {
                        outageAnnounced = true
                        alertManager.status(message)
                    }
                }
            }
            "error" -> {
                val code = payload.optString("code")
                when {
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
                        settleFrame()
                        onState("Server busy; shedding frames")
                    }
                    code == "vision_unavailable" -> {
                        sessionReady = false
                        visionEnabled = false
                        settleFrame()
                        val message = "Vision assistance unavailable. Use cane or guide."
                        onState(message)
                        if (!outageAnnounced) {
                            outageAnnounced = true
                            alertManager.status(message)
                        }
                        // The server will close this socket after the error. Closing proactively
                        // also protects older deployments that do not, and starts normal backoff.
                        socket?.close(1011, "vision unavailable")
                    }
                    else -> {
                        Log.w("AkshravaDebug", "ws_hard_error code=$code")
                        settleFrame()
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
                        alertManager.status(message)
                    }
                } else {
                    cloudFallbackWarningAnnounced = false
                }
                val frameMono = payload.optLong("capture_mono_ms", -1)
                val age = if (frameMono >= 0) SystemClock.elapsedRealtime() - frameMono else Long.MAX_VALUE
                val priority = payload.optBoolean("priority", false)
                // Any result (including look) settles the in-flight slot; cancel the timeout first.
                cancelSettleTimeout()
                consecutiveSettleTimeouts = 0
                val sentAt = frameSentAtMonoMs
                if (sentAt > 0L) {
                    onRoundTripMs(SystemClock.elapsedRealtime() - sentAt)
                }
                val hazard = payload.optJSONObject("hazard")
                val isUrgent = hazard?.optString("level") == "urgent"
                // Look answers use the full freshness budget even if the hazard is S1 —
                // a user-pulled query must not be dropped by the tighter S1 window on slow links.
                val maxAge = when {
                    priority -> LOOK_FRESHNESS_MS.coerceAtLeast(configuredStaleAlertMs)
                    isUrgent -> URGENT_FRESHNESS_MS.coerceAtLeast(configuredStaleAlertMs)
                    else -> configuredStaleAlertMs
                }
                val detectionCount = payload.optInt("detection_count", -1)
                val labels = payload.optJSONArray("detection_labels")
                val labelValues = buildList {
                    if (labels != null) for (i in 0 until labels.length()) add(labels.optString(i))
                }
                val lateSuppressed = payload.optBoolean("late_suppressed", false)
                Log.i(
                    "AkshravaVision",
                    "frame=${payload.optLong("frame_id", -1)} detections=$detectionCount labels=$labelValues " +
                        "late_suppressed=$lateSuppressed result_age_ms=$age priority=$priority"
                )
                // #region agent log
                AgentDebugLog.log(
                    "H3",
                    "ProtocolClient.handleMessage:result",
                    "ws_result",
                    mapOf(
                        "frameId" to payload.optLong("frame_id", -1),
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
                        frameId = payload.optLong("frame_id", -1),
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
                    val lookSummary = payload.optString("look_summary", "").ifBlank {
                        hazard?.optString("spoken_preview", "") ?: ""
                    }
                    if (priority && lookSummary.isNotBlank()) {
                        alertManager.speakComposed(lookSummary, urgent = true)
                        onState("Live · ${hazard?.optString("message_key") ?: labelHint ?: "look"}")
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
                settleFrame()
            }
        }
    }

    private fun handlePermanentFailure(message: String) {
        settleFrame()
        cancelSettleTimeout()
        cancelAppPing()
        sessionReady = false
        visionEnabled = false
        closedByUser = true
        onState(message)
        alertManager.status(message)
        pendingReconnect?.cancel(false)
        reconnect.shutdownNow()
    }

    private fun handleDrop(cause: String) {
        settleFrame()
        cancelSettleTimeout()
        cancelAppPing()
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
        if (!outageAnnounced) {
            outageAnnounced = true
            // No local detector is bundled. Do not imply that the phone can still see after the
            // server link is lost.
            val message = "Vision assistance unavailable. Use cane or guide."
            onState(message)
            alertManager.status(message)
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
        }.getOrNull()
    }

    private fun cancelAppPing() {
        pendingAppPing?.cancel(false)
        pendingAppPing = null
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
        pendingReconnect = runCatching {
            reconnect.schedule({
                logConnection("reconnect_executing", mapOf("attempt" to attempt))
                openSocket("reconnect")
            }, delayMs, TimeUnit.MILLISECONDS)
        }.getOrNull()
    }

    @Volatile private var configuredStaleAlertMs: Long = STALE_ALERT_MS

    private fun settleFrame() {
        cancelSettleTimeout()
        if (inFlight.getAndSet(false)) onFrameSettled()
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
        sessionReady = false
        visionEnabled = false
        socket?.close(1000, "user stopped")
        socket?.cancel()
        socket = null
        settleFrame()
        reconnect.shutdownNow()
        http.dispatcher.executorService.shutdown()
    }

    /** Forwards OkHttp callbacks only when they belong to the current connection generation. */
    private inner class GenerationGuard(private val generation: Int) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!isCurrentGeneration(generation)) return
            handleOpen()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!isCurrentGeneration(generation)) return
            handleMessage(text)
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
                val message = if (code == 4403) {
                    "Device access has been revoked. Ask a volunteer to provision this phone."
                } else {
                    "Device authentication failed. Ask a volunteer to provision a new token."
                }
                handlePermanentFailure(message)
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
                handlePermanentFailure("Device authentication failed. Ask a volunteer to provision a new token.")
            } else {
                handleDrop("failure_${transportFailureClass(response?.code)}")
            }
        }
    }
}
