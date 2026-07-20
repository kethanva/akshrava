package org.akshrava.app

/**
 * Pure capture-rate policy for the active assistance session.
 *
 * AssistService owns platform I/O; this object owns the "how fast should we sample right now?"
 * decision so thermal/battery/server-quality interactions stay unit-testable across Android
 * releases.
 */
class CapturePolicy {
    companion object {
        private const val MIN_FPS = 0.2
        private const val THROTTLED_FPS = 0.5
        // Stationary still needs >= 2 tracker hits for S2 (~1 s apart with cloud RTT).
        // 0.2 FPS made a standing look take 10+ s before the first caution could fire.
        private const val STATIONARY_FPS = 1.0
        private const val MAX_FPS = 3.0
        private const val HIGH_ALERT_FPS = 2.0
        private const val BATTERY_LOW_FPS = 0.2
        private const val HIGH_ALERT_WINDOW_MS = 10_000L
    }

    @Volatile
    var thermalThrottled: Boolean = false

    @Volatile
    var batteryLow: Boolean = false

    @Volatile
    var quality: Quality = Quality()

    @Volatile
    private var highAlertUntilMs: Long = 0L

    fun markHighAlert(nowMs: Long) {
        highAlertUntilMs = nowMs + HIGH_ALERT_WINDOW_MS
    }

    fun captureIntervalMs(nowMs: Long, motionState: MotionState): Long {
        val targetFps = when {
            thermalThrottled -> THROTTLED_FPS
            batteryLow -> BATTERY_LOW_FPS
            nowMs < highAlertUntilMs -> HIGH_ALERT_FPS
            motionState == MotionState.STATIONARY -> STATIONARY_FPS
            else -> quality.fps
        }.coerceIn(MIN_FPS, MAX_FPS)
        return (1000.0 / targetFps).toLong()
    }
}
