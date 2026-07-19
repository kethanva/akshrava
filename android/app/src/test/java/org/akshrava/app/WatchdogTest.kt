package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Test

class WatchdogTest {
    @Test
    fun livenessCheckIsBoundedToThreeMinutes() {
        assertEquals(3 * 60_000L, Watchdog.INTERVAL_MS)
    }
}
