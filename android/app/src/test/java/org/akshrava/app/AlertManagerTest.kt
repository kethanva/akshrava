package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock

class AlertManagerTest {
    @Test
    fun deferralDelayIsNullOnceUtteranceGapHasElapsed() {
        assertNull(AlertManager.deferralDelayMs(nowMs = 5_000L, lastUtteranceMs = 3_000L))
        assertNull(AlertManager.deferralDelayMs(nowMs = 5_000L, lastUtteranceMs = 3_000L, gapMs = 2_000L))
    }

    @Test
    fun deferralDelayCoversRemainingUtteranceGap() {
        assertEquals(500L, AlertManager.deferralDelayMs(nowMs = 3_500L, lastUtteranceMs = 2_000L))
        assertEquals(2_000L, AlertManager.deferralDelayMs(nowMs = 1_000L, lastUtteranceMs = 1_000L))
        assertTrue(AlertManager.MIN_UTTERANCE_GAP_MS < AlertManager.OBJECT_COOLDOWN_MS)
    }

    @Test
    fun engineRebuildAllowedPolicy() {
        assertTrue(AlertManager.engineRebuildAllowed(10_000L, 0L, 0))
        assertFalse(AlertManager.engineRebuildAllowed(10_000L, 8_000L, AlertManager.ENGINE_REBUILD_MAX_STREAK))
    }

    @Test
    fun alertManagerMockable() {
        val mockAlertManager = mock(AlertManager::class.java)
        assertNotNull(mockAlertManager)
        mockAlertManager.status("Test status message")
        mockAlertManager.muteFor(15)
        mockAlertManager.repeatLast()
        mockAlertManager.shutdown()
    }
}
