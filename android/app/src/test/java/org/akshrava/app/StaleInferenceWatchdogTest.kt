package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * F-30 stale-inference earcon policy.
 *
 * The regression guarded here is a beep that never stops: the tick used to key off "time since the
 * last result", which a healthy but slow-cadence session trips continuously.
 */
class StaleInferenceWatchdogTest {

    @Test
    fun ticksWhileAFrameIsOutstandingPastTheThreshold() {
        assertTrue(
            ProtocolClient.shouldTickStaleInference(
                inFlight = true,
                frameAgeMs = ProtocolClient.STALE_INFERENCE_TICK_AFTER_MS,
                ticksAlready = 0
            )
        )
    }

    @Test
    fun staysSilentWhenNoFrameIsOutstanding() {
        // A slow capture cadence is not a stall. CapturePolicy legitimately reaches a 5 s interval
        // on low battery and 2 s under thermal throttle, both far past the 3 s tick threshold.
        assertFalse(
            ProtocolClient.shouldTickStaleInference(
                inFlight = false,
                frameAgeMs = 5_000L,
                ticksAlready = 0
            )
        )
    }

    @Test
    fun staysSilentBeforeTheThreshold() {
        assertFalse(
            ProtocolClient.shouldTickStaleInference(
                inFlight = true,
                frameAgeMs = ProtocolClient.STALE_INFERENCE_TICK_AFTER_MS - 1,
                ticksAlready = 0
            )
        )
    }

    @Test
    fun stopsAfterTheTickBudgetIsSpent() {
        val stuckForever = ProtocolClient.FRAME_SETTLE_TIMEOUT_MS * 10
        assertTrue(
            ProtocolClient.shouldTickStaleInference(
                inFlight = true,
                frameAgeMs = stuckForever,
                ticksAlready = ProtocolClient.STALE_INFERENCE_MAX_TICKS - 1
            )
        )
        assertFalse(
            ProtocolClient.shouldTickStaleInference(
                inFlight = true,
                frameAgeMs = stuckForever,
                ticksAlready = ProtocolClient.STALE_INFERENCE_MAX_TICKS
            )
        )
    }

    @Test
    fun tickBudgetIsExhaustedWellInsideTheSettleTimeout() {
        // The settle timeout is the real recovery path; the earcon only flags the wait.
        val lastTickAtMs = ProtocolClient.STALE_INFERENCE_TICK_AFTER_MS +
            ProtocolClient.STALE_INFERENCE_TICK_PERIOD_MS * (ProtocolClient.STALE_INFERENCE_MAX_TICKS - 1)
        assertTrue(lastTickAtMs < ProtocolClient.FRAME_SETTLE_TIMEOUT_MS)
    }
}
