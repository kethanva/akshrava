package org.akshrava.app

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.SystemClock
import android.view.Surface
import android.view.WindowManager
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlin.math.sqrt

data class PoseSnapshot(val pitchCdeg: Int?, val rollCdeg: Int?, val ageMs: Long?)

enum class MotionState { STATIONARY, WALKING, TURNING }

/**
 * Supplies a filtered device pose plus a coarse motion state.
 * Pose is sent to invalidate geometry, never to manufacture range.
 * Motion state only drives the capture rate (§3.2); it makes no safety claim.
 * Thresholds are conservative starting points and are flagged for per-device
 * calibration during provisioning (Appendix low-confidence).
 */
class PoseTracker(context: Context) : SensorEventListener {
    private companion object {
        const val WALKING_MAD_THRESHOLD = 0.9f       // m/s^2 mean abs deviation of |accel|
        const val TURN_RATE_THRESHOLD = 0.9f         // rad/s (~50 deg/s)
        const val EMA_ALPHA = 0.2f
        const val SENSOR_PERIOD_US = 40_000          // 25 Hz: enough for gating, gentler on old phones
    }

    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val displayRotation = (context.getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.rotation
    private val rotation = FloatArray(9)
    private val remappedRotation = FloatArray(9)
    private val orientation = FloatArray(3)
    @Volatile private var pitch: Int? = null
    @Volatile private var roll: Int? = null
    @Volatile private var timestampMs: Long? = null

    private var accelEma = 9.81f
    @Volatile private var accelMad = 0f
    @Volatile private var gyroMagnitude = 0f
    @Volatile private var turnPending = false

    fun start() {
        val orientationSensor = sensorManager.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
        orientationSensor?.let { sensorManager.registerListener(this, it, SENSOR_PERIOD_US) }
        sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)?.let {
            sensorManager.registerListener(this, it, SENSOR_PERIOD_US)
        }
        sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let {
            sensorManager.registerListener(this, it, SENSOR_PERIOD_US)
        }
    }

    fun stop() = sensorManager.unregisterListener(this)

    fun snapshot(): PoseSnapshot {
        val updated = timestampMs
        return PoseSnapshot(pitch, roll, updated?.let { SystemClock.elapsedRealtime() - it })
    }

    fun motionState(): MotionState = when {
        gyroMagnitude > TURN_RATE_THRESHOLD -> MotionState.TURNING
        accelMad > WALKING_MAD_THRESHOLD -> MotionState.WALKING
        else -> MotionState.STATIONARY
    }

    /** Edge-triggered: a fresh turn asks for one immediate frame, consumed once. */
    fun consumeTurn(): Boolean {
        if (!turnPending) return false
        turnPending = false
        return true
    }

    override fun onSensorChanged(event: SensorEvent) {
        when (event.sensor.type) {
            Sensor.TYPE_GAME_ROTATION_VECTOR, Sensor.TYPE_ROTATION_VECTOR -> {
                SensorManager.getRotationMatrixFromVector(rotation, event.values)
                val (axisX, axisY) = when (displayRotation) {
                    Surface.ROTATION_90 -> SensorManager.AXIS_Y to SensorManager.AXIS_MINUS_X
                    Surface.ROTATION_180 -> SensorManager.AXIS_MINUS_X to SensorManager.AXIS_MINUS_Y
                    Surface.ROTATION_270 -> SensorManager.AXIS_MINUS_Y to SensorManager.AXIS_X
                    else -> SensorManager.AXIS_X to SensorManager.AXIS_Y
                }
                SensorManager.remapCoordinateSystem(rotation, axisX, axisY, remappedRotation)
                SensorManager.getOrientation(remappedRotation, orientation)
                // Sensor coordinates are calibrated during provisioning; values are only validity evidence.
                pitch = Math.toDegrees(orientation[1].toDouble()).times(100).roundToInt()
                roll = Math.toDegrees(orientation[2].toDouble()).times(100).roundToInt()
                timestampMs = SystemClock.elapsedRealtime()
            }
            Sensor.TYPE_ACCELEROMETER -> {
                val magnitude = sqrt(event.values[0] * event.values[0] + event.values[1] * event.values[1] + event.values[2] * event.values[2])
                val deviation = abs(magnitude - accelEma)
                accelEma += EMA_ALPHA * (magnitude - accelEma)
                accelMad += EMA_ALPHA * (deviation - accelMad)
            }
            Sensor.TYPE_GYROSCOPE -> {
                val magnitude = sqrt(event.values[0] * event.values[0] + event.values[1] * event.values[1] + event.values[2] * event.values[2])
                gyroMagnitude = magnitude
                if (magnitude > TURN_RATE_THRESHOLD) turnPending = true
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
}
