package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.mock

class WatchdogTest {
    @Test
    fun livenessCheckIsBoundedToThreeMinutes() {
        assertEquals(3 * 60_000L, Watchdog.INTERVAL_MS)
    }

    @Test
    fun watchdogMockable() {
        val mockWatchdog = mock(Watchdog::class.java)
        assertNotNull(mockWatchdog)
    }
}
