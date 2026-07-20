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
    private val language: String = "en",
    private val http: OkHttpClient = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()
) : WebSocketListener() {
    internal companion object {
        const val MAX_BACKOFF_ATTEMPT = 4          // 2^4 = 16 s, capped to 10 s
        const val MAX_BACKOFF_SECONDS = 10.0
        /**
         * Cloud remote YOLO RTT is typically 400–900 ms (often >1 s on LTE).
         * Match the server pilot budget (ALERT_MAX_AGE_MS=2500) so real detections
         * are spoken instead of silently discarded as "stale".
         */
        const val STALE_ALERT_MS = 2500L
        /** Look answers use the full freshness budget even when the hazard is S1. */
        const val LOOK_FRESHNESS_MS = 2500L
        const val URGENT_FRESHNESS_MS = 1500L
        /**
         * Must cover CPU remote YOLO (GCP pilot uses up to ~9s inference). A shorter budget
         * cancels a healthy socket mid-infer and destroys end-to-end frame throughput.
         */
        const val FRAME_SETTLE_TIMEOUT_MS = 10_000L
        /** Look answers use the same settle budget; announce failure if unanswered. */
        const val LOOK_TIMEOUT_MS = FRAME_SETTLE_TIMEOUT_MS
        /** Soft timeouts shed quality first; only repeated hangs tear down the socket. */
        const val SETTLE_TIMEOUTS_BEFORE_RECONNECT = 2

        /** Device revocation is an operator action, not a network condition to retry. */
        fun isPermanentAccessClose(code: Int): Boolean = code == 4401 || code == 4403

        /** Wire contract uses en|hi; AppConfig stores BCP-47 tags like en-IN / hi-IN. */
        fun wireLanguage(tag: String): String =
            if (tag.lowercase().startsWith("hi")) "hi" else "en"
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
    @Volatile private var pendingLook = false
    @Volatile private var frameSentAtMonoMs = 0L
    @Volatile private var consecutiveSettleTimeouts = 0

    fun connect() {
        if (endpoint.isBlank() || token.isBlank()) {
            onState("Provisioning required")
            return
        }
        closedByUser = false
        openSocket()
    }

    private fun openSocket() {
        if (closedByUser) return
        pendingReconnect?.cancel(false)
        pendingReconnect = null
        val previous = socket
        socket = null
        previous?.cancel()
        val generation = connectionGeneration.incrementAndGet()
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
    fun canStream(): Boolean = sessionReady && visionEnabled

    fun sendFrame(
        frameId: Long,
        captureMonoMs: Long,
        pose: PoseSnapshot,
        calibrationId: String,
        frame: EncodedFrame,
        mode: String = "normal",
        priority: Boolean = false
    ): Boolean {
        val look = priority || mode == "priority"
        val ws = socket ?: return failSendFrame(look)
        // Do not produce traffic or imply an active service before the authenticated server has
        // explicitly confirmed that a real detector, rather than bench-mode NoopDetector, is live.
        if (!canStream()) return failSendFrame(look)
        if (!inFlight.compareAndSet(false, true)) return failSendFrame(look)
        val header = JSONObject()
            .put("type", "frame")
            .put("id", frameId)
            .put("capture_mono_ms", captureMonoMs)
            .put("capture_epoch_ms", System.currentTimeMillis())
            .put("w", frame.width)
            .put("h", frame.height)
            .put("jpeg_bytes", frame.jpeg.size)
            .put("camera_calibration_id", calibrationId)
            .put("pitch_cdeg", pose.pitchCdeg)
            .put("roll_cdeg", pose.rollCdeg)
            .put("pose_age_ms", pose.ageMs)
            .put("mode", if (look) "priority" else mode)
            .put("priority", look)
            .put("language", wireLanguage(language))
            .put("trace_id", "frame-$frameId-$captureMonoMs")
        // Header and JPEG are a pair in the server protocol.  OkHttp queues WebSocket messages
        // independently, so if it accepts the header but rejects the JPEG we must tear down the
        // socket rather than let the next JPEG attach to this header.
        if (!ws.send(header.toString())) {
            settleFrame()
            return failSendFrame(look)
        }
        if (!ws.send(frame.jpeg.toByteString())) {
            ws.close(1011, "incomplete frame")
            settleFrame()
            return failSendFrame(look)
        }
        frameSentAtMonoMs = SystemClock.elapsedRealtime()
        scheduleSettleTimeout(look)
        return true
    }

    /** Every control action must be confirmed by voice (§6.4): an explicit look that never
     * even made it onto the wire must not resolve into silence just because it wasn't sent. */
    private fun failSendFrame(isLook: Boolean): Boolean {
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
                    scheduleReconnect()
                }
            }, FRAME_SETTLE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        }.getOrNull()
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
        reconnectAttempt = 0
        sessionReady = false
        visionEnabled = false
        cloudFallbackWarningAnnounced = false
        if (outageAnnounced) {
            outageAnnounced = false
            alertManager.status("Connection restored")
        }
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
                val advertised = payload.optInt("max_in_flight", 1).coerceIn(1, 2)
                maxInFlight = advertised
                if (visionEnabled) {
                    onState("Vision assistance connected")
                } else {
                    val message = "Vision model unavailable. Use cane or guide."
                    onState(message)
                    alertManager.status(message)
                }
            }
            "error" -> {
                when (payload.optString("code")) {
                    "worker_saturated", "frame_rate_limited" -> {
                        // Soft shed: keep socket, free in-flight slot, let the next frame retry.
                        settleFrame()
                        onState("Server busy; shedding frames")
                    }
                    "vision_unavailable" -> {
                        sessionReady = false
                        visionEnabled = false
                        settleFrame()
                        val message = "Vision assistance unavailable. Use cane or guide."
                        onState(message)
                        alertManager.status(message)
                        // The server will close this socket after the error. Closing proactively
                        // also protects older deployments that do not, and starts normal backoff.
                        socket?.close(1011, "vision unavailable")
                    }
                    else -> {
                        settleFrame()
                        onState("Server protocol error")
                    }
                }
            }
            "quality" -> onQuality(Quality.fromServer(
                payload.optInt("max_side", 640),
                payload.optInt("jpeg_q", 55),
                payload.optDouble("fps", 1.0)
            ))
            "result" -> {
                Log.i(
                    "AkshravaVision",
                    "frame=${payload.optLong("frame_id", -1)} detections=${payload.optInt("detection_count", -1)} " +
                        "labels=${payload.optJSONArray("detection_labels") ?: "unknown"} hazard=${payload.has("hazard") && !payload.isNull("hazard")}"
                )
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
                    priority -> LOOK_FRESHNESS_MS
                    isUrgent -> URGENT_FRESHNESS_MS
                    else -> STALE_ALERT_MS
                }
                val detectionCount = payload.optInt("detection_count", -1)
                val labels = payload.optJSONArray("detection_labels")
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
                        onState("Live · $labelHint")
                    }
                } else if (labelHint != null) {
                    // Still surface detector output when speech was suppressed as late.
                    onState("Live · $labelHint")
                }
                settleFrame()
            }
        }
    }

    private fun handlePermanentFailure(message: String) {
        settleFrame()
        cancelSettleTimeout()
        sessionReady = false
        visionEnabled = false
        closedByUser = true
        onState(message)
        alertManager.status(message)
        pendingReconnect?.cancel(false)
        reconnect.shutdownNow()
    }

    private fun handleDrop() {
        settleFrame()
        cancelSettleTimeout()
        sessionReady = false
        visionEnabled = false
        if (closedByUser) return
        if (!outageAnnounced) {
            outageAnnounced = true
            // No local detector is bundled. Do not imply that the phone can still see after the
            // server link is lost.
            val message = "Vision assistance unavailable. Use cane or guide."
            onState(message)
            alertManager.status(message)
        }
        scheduleReconnect()
    }

    private fun scheduleReconnect() {
        if (closedByUser) return
        pendingReconnect?.cancel(false)
        val backoffSeconds = min(MAX_BACKOFF_SECONDS, 2.0.pow(reconnectAttempt.toDouble()))
        reconnectAttempt = (reconnectAttempt + 1).coerceAtMost(MAX_BACKOFF_ATTEMPT)
        val delayMs = ((backoffSeconds + Random.nextDouble(0.0, 0.5)) * 1000).toLong()
        pendingReconnect = runCatching {
            reconnect.schedule({ openSocket() }, delayMs, TimeUnit.MILLISECONDS)
        }.getOrNull()
    }

    private fun settleFrame() {
        cancelSettleTimeout()
        if (inFlight.getAndSet(false)) onFrameSettled()
    }

    fun close() {
        closedByUser = true
        connectionGeneration.incrementAndGet()
        pendingReconnect?.cancel(false)
        pendingReconnect = null
        cancelSettleTimeout()
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
            settleFrame()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrentGeneration(generation)) return
            if (isPermanentAccessClose(code)) {
                val message = if (code == 4403) {
                    "Device access has been revoked. Ask a volunteer to provision this phone."
                } else {
                    "Device authentication failed. Ask a volunteer to provision a new token."
                }
                handlePermanentFailure(message)
            } else {
                handleDrop()
            }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!isCurrentGeneration(generation)) return
            if (response?.code == 401 || response?.code == 403) {
                handlePermanentFailure("Device authentication failed. Ask a volunteer to provision a new token.")
            } else {
                handleDrop()
            }
        }
    }
}
