package org.akshrava.app

import kotlin.math.sqrt

/**
 * Double-shake gesture trigger (F-31) for users whose hands are busy with a cane or a guide dog
 * harness and cannot find the headset button.
 *
 * Sample-driven on purpose: [PoseTracker] already streams the accelerometer at 25 Hz for motion
 * state, and this engine is fed from that same callback. It previously registered a second
 * SensorEventListener on the same sensor — registering twice does not share a delivery stream,
 * it adds one, so every accelerometer event was dispatched to the app twice for the whole life of
 * a walk. Taking the samples that already arrive costs nothing and removes a listener.
 *
 * Holds no Context, Handler, or SensorManager: the caller decides which thread [onDoubleShake]
 * runs on. AssistService hops to the main thread there, because the callback speaks and touches
 * session state and must not block sensor delivery for PoseTracker.
 */
class GestureDetectorEngine(
    private val onDoubleShake: () -> Unit
) {

    private var lastUpdateMs = 0L
    private var lastShakeMs = 0L
    /** null = never triggered this session. See the cooldown check for why 0 will not do. */
    private var lastTriggerMs: Long? = null
    private var shakeCount = 0
    private var lastX = 0f
    private var lastY = 0f
    private var lastZ = 0f
    private var seededSample = false

    /** Forget partial gesture state, e.g. when a session starts and the phone has been moved. */
    fun reset() {
        lastUpdateMs = 0L
        lastShakeMs = 0L
        shakeCount = 0
        seededSample = false
    }

    /**
     * Feed one accelerometer reading. Safe to call at the full sensor rate — samples closer
     * together than [MIN_SAMPLE_GAP_MS] are ignored, since shake energy is only a few Hz.
     */
    fun onAccelerometerSample(x: Float, y: Float, z: Float, nowMs: Long) {
        val diffMs = nowMs - lastUpdateMs
        if (diffMs < MIN_SAMPLE_GAP_MS) return
        lastUpdateMs = nowMs

        // The first sample has no predecessor to difference against; comparing it to (0,0,0)
        // measures gravity, not a shake, and read as a shake on every single start().
        if (!seededSample) {
            seededSample = true
            lastX = x; lastY = y; lastZ = z
            return
        }

        val dx = x - lastX
        val dy = y - lastY
        val dz = z - lastZ
        lastX = x; lastY = y; lastZ = z
        val speed = sqrt(dx * dx + dy * dy + dz * dz) / diffMs * 10_000
        if (speed <= SHAKE_THRESHOLD) return

        // One vigorous movement spans several samples; only count it once.
        if (nowMs - lastShakeMs <= SHAKE_DEBOUNCE_MS) return
        if (nowMs - lastShakeMs > SHAKE_TIMEOUT_MS) shakeCount = 0
        shakeCount += 1
        lastShakeMs = nowMs
        if (shakeCount < SHAKES_REQUIRED) return

        shakeCount = 0
        // Walking briskly with the phone on a lanyard can clear the threshold. Rate-limit the
        // trigger so a jolting bus ride cannot turn into a stream of look requests.
        //
        // The "never triggered yet" case is explicit rather than a 0 sentinel: timestamps here are
        // SystemClock.elapsedRealtime(), which is time since boot, so on a phone that started
        // assistance within TRIGGER_COOLDOWN_MS of booting a 0 default reads as "just triggered"
        // and silently swallows the user's first gesture.
        val previousTrigger = lastTriggerMs
        if (previousTrigger != null && nowMs - previousTrigger < TRIGGER_COOLDOWN_MS) return
        lastTriggerMs = nowMs
        onDoubleShake()
    }

    companion object {
        /** Shake energy is a few Hz; 100 ms between differenced samples resolves it. */
        const val MIN_SAMPLE_GAP_MS = 100L
        const val SHAKE_THRESHOLD = 800f
        const val SHAKES_REQUIRED = 2
        /** Both shakes must land inside this window to count as one deliberate gesture. */
        const val SHAKE_TIMEOUT_MS = 1_000L
        const val SHAKE_DEBOUNCE_MS = 250L
        const val TRIGGER_COOLDOWN_MS = 3_000L

        /** Pure form of the counting rule, so the gesture window is testable without a sensor. */
        fun isDoubleShake(shakeTimestampsMs: List<Long>): Boolean {
            var count = 0
            var previous = Long.MIN_VALUE
            for (t in shakeTimestampsMs) {
                if (previous != Long.MIN_VALUE && t - previous <= SHAKE_DEBOUNCE_MS) continue
                if (previous == Long.MIN_VALUE || t - previous > SHAKE_TIMEOUT_MS) count = 0
                count += 1
                previous = t
                if (count >= SHAKES_REQUIRED) return true
            }
            return false
        }
    }
}
