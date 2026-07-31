package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GestureDetectorEngineTest {

    /** One sample pair far enough apart in value to clear SHAKE_THRESHOLD at the given gap. */
    private fun GestureDetectorEngine.shakeAt(nowMs: Long, magnitude: Float = 40f) {
        onAccelerometerSample(magnitude, 0f, 0f, nowMs)
    }

    @Test
    fun twoShakesInsideTheWindowTrigger() {
        assertTrue(GestureDetectorEngine.isDoubleShake(listOf(0L, 400L)))
    }

    @Test
    fun oneShakeDoesNotTrigger() {
        assertFalse(GestureDetectorEngine.isDoubleShake(listOf(0L)))
    }

    @Test
    fun samplesInsideTheDebounceCountAsOneShake() {
        assertFalse(GestureDetectorEngine.isDoubleShake(listOf(0L, 100L, 200L)))
    }

    @Test
    fun shakesFurtherApartThanTheWindowRestartTheCount() {
        assertFalse(
            GestureDetectorEngine.isDoubleShake(
                listOf(0L, GestureDetectorEngine.SHAKE_TIMEOUT_MS + 1)
            )
        )
    }

    @Test
    fun sustainedJoltingEventuallyPairsButOnlyOnAdjacentShakes() {
        assertTrue(GestureDetectorEngine.isDoubleShake(listOf(0L, 400L, 800L)))
    }

    @Test
    fun firstSampleIsSeededAndNeverReadAsAShake() {
        // Differencing sample one against (0,0,0) measures gravity, which used to fire a shake
        // on every session start.
        var triggers = 0
        val engine = GestureDetectorEngine { triggers += 1 }
        engine.shakeAt(1_000L)
        engine.shakeAt(1_200L)
        assertEquals(0, triggers)
    }

    @Test
    fun twoDistinctShakesInsideTheWindowFireOnce() {
        var triggers = 0
        val engine = GestureDetectorEngine { triggers += 1 }
        engine.onAccelerometerSample(0f, 0f, 0f, 1_000L)   // seed
        engine.shakeAt(1_150L)                             // shake 1
        // Past SHAKE_DEBOUNCE_MS (one vigorous movement spans several samples) but still inside
        // SHAKE_TIMEOUT_MS, so this counts as the second half of one deliberate gesture.
        engine.onAccelerometerSample(0f, 0f, 0f, 1_500L)   // shake 2 (large delta back)
        assertEquals(1, triggers)
    }

    @Test
    fun firstGestureFiresEvenMomentsAfterBoot() {
        // Timestamps are elapsedRealtime (time since boot). A 0 default for "last triggered"
        // reads as "just triggered" on a freshly booted phone and ate the user's first gesture.
        var triggers = 0
        val engine = GestureDetectorEngine { triggers += 1 }
        engine.onAccelerometerSample(0f, 0f, 0f, 120L)
        engine.shakeAt(280L)
        engine.onAccelerometerSample(0f, 0f, 0f, 640L)
        assertEquals(1, triggers)
    }

    @Test
    fun samplesArrivingFasterThanTheSampleGapAreIgnored() {
        var triggers = 0
        val engine = GestureDetectorEngine { triggers += 1 }
        engine.onAccelerometerSample(0f, 0f, 0f, 1_000L)
        // 25 Hz delivery = 40 ms apart; the engine only differences every MIN_SAMPLE_GAP_MS.
        var t = 1_000L
        repeat(4) {
            t += 40L
            engine.shakeAt(t)
        }
        assertEquals(0, triggers)
    }

    @Test
    fun afterTriggeringTheCooldownSuppressesAJoltingRide() {
        var triggers = 0
        val engine = GestureDetectorEngine { triggers += 1 }
        engine.onAccelerometerSample(0f, 0f, 0f, 1_000L)
        engine.shakeAt(1_150L)
        engine.onAccelerometerSample(0f, 0f, 0f, 1_500L)
        assertEquals(1, triggers)

        // A second deliberate pair inside TRIGGER_COOLDOWN_MS must not queue another look.
        engine.shakeAt(1_850L)
        engine.onAccelerometerSample(0f, 0f, 0f, 2_200L)
        assertEquals(1, triggers)
    }

    @Test
    fun resetClearsPartialGestureState() {
        var triggers = 0
        val engine = GestureDetectorEngine { triggers += 1 }
        engine.onAccelerometerSample(0f, 0f, 0f, 1_000L)
        engine.shakeAt(1_150L)   // one shake banked
        engine.reset()
        // The next sample re-seeds, so the banked shake cannot pair with it.
        engine.onAccelerometerSample(0f, 0f, 0f, 1_300L)
        engine.shakeAt(1_650L)
        assertEquals(0, triggers)
    }
}
