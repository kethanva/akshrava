package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** F-15 auditory battery gauge wording. */
class BatteryGaugeTest {

    @Test
    fun reportsPercentAndAnEstimate() {
        val text = DeviceCapability.batteryStatusText(80)
        assertTrue(text, text.contains("80 percent"))
        assertTrue(text, text.contains("roughly 20 hours"))
    }

    @Test
    fun neverClaimsZeroHoursWhileThePhoneIsStillRunning() {
        // Integer division used to say "About 0 hours of vision time remaining" at 3%, which reads
        // as "the session is already over" to someone deciding whether to set out.
        val zeroHours = Regex("\\b0 hours\\b")
        for (pct in 1..100) {
            val text = DeviceCapability.batteryStatusText(pct)
            assertTrue(text, !zeroHours.containsMatchIn(text))
        }
    }

    @Test
    fun lowBatteryDegradesToACoarsePhrase() {
        assertTrue(DeviceCapability.batteryStatusText(2).contains("less than an hour"))
        assertTrue(DeviceCapability.batteryStatusText(4).contains("roughly one hour"))
    }

    @Test
    fun unknownLevelSaysSoRatherThanGuessing() {
        assertEquals("Battery level unavailable.", DeviceCapability.batteryStatusText(null))
    }

    @Test
    fun estimateIsAlwaysMarkedAsAnEstimate() {
        // The drain figure is a single coarse constant; the wording must not imply a measurement.
        for (pct in listOf(1, 15, 50, 100)) {
            val text = DeviceCapability.batteryStatusText(pct)
            assertTrue(text, text.contains("Estimated"))
        }
    }
}
