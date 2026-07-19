package org.akshrava.app

import android.os.SystemClock
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

data class Quality(val maxSide: Int = 640, val jpegQ: Int = 60, val fps: Double = 1.0) {
    companion object {
        /** Server guidance is advisory; never let a malformed response raise phone cost. */
        fun fromServer(maxSide: Int, jpegQ: Int, fps: Double) = Quality(
            maxSide = maxSide.coerceIn(320, 640),
            jpegQ = jpegQ.coerceIn(35, 70),
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
    private val onHighAlert: () -> Unit = {}
) : WebSocketListener() {
    private companion object {
        const val MAX_BACKOFF_ATTEMPT = 4          // 2^4 = 16 s, capped to 10 s
        const val MAX_BACKOFF_SECONDS = 10.0
        const val STALE_ALERT_MS = 500L
    }

    private val http = OkHttpClient.Builder().pingInterval(20, TimeUnit.SECONDS).build()
    private val reconnect: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor()
    private val inFlight = AtomicBoolean(false)
    private var socket: WebSocket? = null
    @Volatile private var closedByUser = false
    @Volatile private var outageAnnounced = false
    @Volatile private var sessionReady = false
    @Volatile private var visionEnabled = false
    @Volatile private var reconnectAttempt = 0
    @Volatile private var cloudFallbackWarningAnnounced = false

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
        socket = http.newWebSocket(
            Request.Builder().url(endpoint).header("Authorization", "Bearer $token").build(), this
        )
    }

    fun sendFrame(
        frameId: Long,
        captureMonoMs: Long,
        pose: PoseSnapshot,
        calibrationId: String,
        frame: EncodedFrame,
        mode: String = "normal"
    ): Boolean {
        val ws = socket ?: return false
        // Do not produce traffic or imply an active service before the authenticated server has
        // explicitly confirmed that a real detector, rather than bench-mode NoopDetector, is live.
        if (!sessionReady || !visionEnabled) return false
        if (!inFlight.compareAndSet(false, true)) return false
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
            .put("mode", mode)
        // Header and JPEG are a pair in the server protocol.  OkHttp queues WebSocket messages
        // independently, so if it accepts the header but rejects the JPEG we must tear down the
        // socket rather than let the next JPEG attach to this header.
        if (!ws.send(header.toString())) {
            settleFrame()
            return false
        }
        if (!ws.send(frame.jpeg.toByteString())) {
            ws.close(1011, "incomplete frame")
            settleFrame()
            return false
        }
        return true
    }

    override fun onOpen(webSocket: WebSocket, response: Response) {
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

    override fun onMessage(webSocket: WebSocket, text: String) {
        val payload = runCatching { JSONObject(text) }.getOrNull() ?: run {
            onState("Invalid server response")
            settleFrame()
            return
        }
        when (payload.optString("type")) {
            "ready" -> {
                sessionReady = true
                visionEnabled = payload.optBoolean("vision_enabled", false)
                if (visionEnabled) {
                    onState("Vision assistance connected")
                } else {
                    val message = "Vision model unavailable. Use cane or guide."
                    onState(message)
                    alertManager.status(message)
                }
            }
            "quality" -> onQuality(Quality.fromServer(
                payload.optInt("max_side", 640),
                payload.optInt("jpeg_q", 60),
                payload.optDouble("fps", 1.0)
            ))
            "result" -> {
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
                // The phone owns freshness; server and phone monotonic clocks are not comparable.
                if (age <= STALE_ALERT_MS) {
                    val hazard = payload.optJSONObject("hazard")
                    if (hazard != null) {
                        val isUrgent = hazard.optString("level") == "urgent"
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
                    }
                }
                settleFrame()
            }
            "error" -> {
                val code = payload.optString("code")
                if (code == "vision_unavailable") {
                    sessionReady = false
                    visionEnabled = false
                    val message = "Vision assistance unavailable. Use cane or guide."
                    onState(message)
                    alertManager.status(message)
                    // The server will close this socket after the error. Closing proactively
                    // also protects older deployments that do not, and starts normal backoff.
                    webSocket.close(1011, "vision unavailable")
                } else {
                    onState("Server protocol error")
                }
                settleFrame()
            }
        }
    }

    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) = settleFrame()

    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
        if (code == 4401) {
            handlePermanentFailure("Device authentication failed. Ask a volunteer to provision a new token.")
        } else {
            handleDrop()
        }
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        if (response?.code == 401 || response?.code == 403) {
            handlePermanentFailure("Device authentication failed. Ask a volunteer to provision a new token.")
        } else {
            handleDrop()
        }
    }

    private fun handlePermanentFailure(message: String) {
        settleFrame()
        sessionReady = false
        visionEnabled = false
        closedByUser = true
        onState(message)
        alertManager.status(message)
        reconnect.shutdownNow()
    }

    private fun handleDrop() {
        settleFrame()
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
        val backoffSeconds = min(MAX_BACKOFF_SECONDS, 2.0.pow(reconnectAttempt.toDouble()))
        reconnectAttempt = (reconnectAttempt + 1).coerceAtMost(MAX_BACKOFF_ATTEMPT)
        val delayMs = ((backoffSeconds + Random.nextDouble(0.0, 0.5)) * 1000).toLong()
        runCatching { reconnect.schedule({ openSocket() }, delayMs, TimeUnit.MILLISECONDS) }
    }

    private fun settleFrame() {
        if (inFlight.getAndSet(false)) onFrameSettled()
    }

    fun close() {
        closedByUser = true
        sessionReady = false
        visionEnabled = false
        socket?.close(1000, "user stopped")
        socket = null
        settleFrame()
        reconnect.shutdownNow()
        http.dispatcher.executorService.shutdown()
    }
}
