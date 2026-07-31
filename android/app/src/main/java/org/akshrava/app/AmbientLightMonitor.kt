package org.akshrava.app

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.SystemClock

enum class AmbientLightLevel { DARK, BRIGHT }

/**
 * Ambient light edge state (F-71). [established] is the level the user has already been told
 * about (or was seeded with at session start); [candidate] is a differing level still serving
 * its hold. Immutable so [AmbientLightMonitor.step] stays a pure function of its inputs.
 */
data class AmbientLightState(
    val established: AmbientLightLevel? = null,
    val candidate: AmbientLightLevel? = null,
    val candidateSinceMs: Long = 0L,
    val lastAnnounceMs: Long? = null
)

/** One step of the edge machine: the next state, and the level to speak (null = stay quiet). */
data class AmbientLightStep(val state: AmbientLightState, val announce: AmbientLightLevel?)

/**
 * Ambient light **edge** context (F-71): tells the user when the environment they are walking
 * through crosses between dark and bright, and nothing else.
 *
 * This is environment light from `TYPE_LIGHT`, which is not the same signal as the camera-luma
 * gates: F-02 occlusion and F-42 glare describe whether *this frame* is usable, and both fire on
 * a covered or sun-facing lens in a perfectly ordinary room. A phone on a lanyard reports the
 * light falling on its front face, so it answers the different question of whether the street
 * itself just got dark — the thing a blind user has no other channel for.
 *
 * Deliberately edge-only and bounded. There is no continuous "light level" tone: a tone in the
 * user's only audio channel for the whole walk costs battery on a donated phone and masks the
 * hazard alerts it would play over. This makes no claim about the path, only about the light.
 *
 * The state machine is a pure function so the thresholds and the cooldown are testable without a
 * SensorManager; the class around it holds nothing but the registration.
 */
class AmbientLightMonitor(
    context: Context,
    private val onEdge: (AmbientLightLevel) -> Unit
) : SensorEventListener {

    companion object {
        /** Below this many lux the environment is dark: roughly a lit corridor at night. */
        const val DARK_LUX = 10f

        /** Above this many lux it is unambiguously bright: daylight, a well-lit shop front. */
        const val BRIGHT_LUX = 50f

        // The gap between the two is a hysteresis band with no verdict at all. Overcast dusk and
        // ordinary indoor lighting sit inside it for minutes, and a single threshold there would
        // chatter across the boundary every few samples.

        /**
         * How long a differing level must hold before it counts as an edge.
         *
         * Walking under an awning, past a shop window or beneath a streetlight changes the reading
         * for a second or two and is the common case, not the event worth speaking.
         */
        const val HOLD_MS = 3_000L

        /** Floor on the gap between two spoken edges (spec: >= 8 s). */
        const val COOLDOWN_MS = 8_000L

        /** 1 Hz. `TYPE_LIGHT` is an on-change sensor; faster buys nothing and costs battery. */
        const val SAMPLE_PERIOD_US = 1_000_000

        /** The confident level for a reading, or null inside the hysteresis band. */
        fun classify(lux: Float): AmbientLightLevel? = when {
            lux < DARK_LUX -> AmbientLightLevel.DARK
            lux > BRIGHT_LUX -> AmbientLightLevel.BRIGHT
            else -> null
        }

        fun step(state: AmbientLightState, lux: Float, nowMs: Long): AmbientLightStep {
            // Band readings are neutral: they neither seed a level nor break a pending edge, so a
            // slow ramp from indoors to daylight still resolves instead of being reset each sample.
            val level = classify(lux) ?: return AmbientLightStep(state, null)

            // The first confident reading of a session establishes "now", it is not a change.
            // Announcing it would open every walk with a line about light nobody asked about.
            val established = state.established
                ?: return AmbientLightStep(state.copy(established = level, candidate = null), null)

            if (level == established) {
                return AmbientLightStep(state.copy(candidate = null, candidateSinceMs = 0L), null)
            }

            val since = if (state.candidate == level) state.candidateSinceMs else nowMs
            if (nowMs - since < HOLD_MS) {
                return AmbientLightStep(
                    state.copy(candidate = level, candidateSinceMs = since),
                    null
                )
            }

            // The edge is real. Adopt it whether or not it gets spoken: holding `established` on
            // the old level through a cooldown would leave the very next sample looking like a
            // fresh edge, and it would fire the instant the cooldown lapsed.
            val last = state.lastAnnounceMs
            val suppressed = last != null && nowMs - last < COOLDOWN_MS
            val settled = AmbientLightState(
                established = level,
                candidate = null,
                candidateSinceMs = 0L,
                lastAnnounceMs = if (suppressed) last else nowMs
            )
            return AmbientLightStep(settled, if (suppressed) null else level)
        }
    }

    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager

    @Volatile
    private var state = AmbientLightState()

    /** Returns false when the phone has no light sensor — common enough on donated hardware. */
    fun start(): Boolean {
        val sensor = sensorManager.getDefaultSensor(Sensor.TYPE_LIGHT) ?: return false
        return sensorManager.registerListener(this, sensor, SAMPLE_PERIOD_US)
    }

    fun stop() = sensorManager.unregisterListener(this)

    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type != Sensor.TYPE_LIGHT) return
        val lux = event.values.firstOrNull() ?: return
        val stepped = step(state, lux, SystemClock.elapsedRealtime())
        state = stepped.state
        // Runs on the sensor thread. The caller decides where the announcement happens.
        stepped.announce?.let(onEdge)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
}
