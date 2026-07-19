package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Test

class CapturePolicyTest {
    @Test
    fun stationaryUsesLowCadenceUnlessHigherPriorityStateExists() {
        val policy = CapturePolicy()
        policy.quality = Quality(fps = 1.0)

        assertEquals(5_000L, policy.captureIntervalMs(1_000L, MotionState.STATIONARY))
    }

    @Test
    fun highAlertTemporarilyRaisesCadence() {
        val policy = CapturePolicy()
        policy.markHighAlert(1_000L)

        assertEquals(500L, policy.captureIntervalMs(1_100L, MotionState.WALKING))
        assertEquals(1_000L, policy.captureIntervalMs(12_000L, MotionState.WALKING))
    }

    @Test
    fun thermalAndBatteryLimitsOverrideServerQualityAndHighAlert() {
        val policy = CapturePolicy()
        policy.quality = Quality(fps = 2.0)
        policy.markHighAlert(1_000L)

        policy.thermalThrottled = true
        assertEquals(2_000L, policy.captureIntervalMs(1_100L, MotionState.WALKING))

        policy.thermalThrottled = false
        policy.batteryLow = true
        assertEquals(5_000L, policy.captureIntervalMs(1_100L, MotionState.WALKING))
    }
}
