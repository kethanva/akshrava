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
    fun headsetMuteIsBoundedAndASecondGestureResumesIt() {
        val now = 10_000L
        val until = AlertManager.nextMuteUntil(now, 0L, AlertManager.MUTE_AUTO_EXPIRE_MS)
        assertEquals(now + 120_000L, until)
        assertEquals(0L, AlertManager.nextMuteUntil(now + 1L, until, AlertManager.MUTE_AUTO_EXPIRE_MS))
        assertTrue(AlertManager.speechSuppressedByMute(now + 1L, until, bypassMute = false))
        assertFalse(AlertManager.speechSuppressedByMute(now + 1L, until, bypassMute = true))
        assertFalse(AlertManager.speechSuppressedByMute(until, until, bypassMute = false))
    }

    @Test
    fun alertManagerMockable() {
        val mockAlertManager = mock(AlertManager::class.java)
        assertNotNull(mockAlertManager)
        mockAlertManager.status("Test status message")
        mockAlertManager.toggleMute()
        mockAlertManager.repeatLast()
        mockAlertManager.shutdown()
    }

    @Test
    fun everyOperationalKeyResolvesInEveryLanguage() {
        val keys = listOf(
            "op_connected", "op_restored", "op_link_lost", "op_vision_unavailable",
            "op_model_unavailable", "op_cloud_fallback_unavailable", "op_server_shedding",
            "op_camera_dark", "op_camera_glare", "op_camera_blurry", "op_camera_failed",
            "op_camera_stalled", "op_analyze_failed", "op_access_revoked", "op_auth_failed",
            "op_session_taken_over", "op_look_unavailable", "op_starting",
            "op_starting_no_cpu_keepalive", "op_starting_no_screen_keepalive",
            "op_phone_tilted", "op_thermal_slow", "op_battery_low",
            "op_battery_critical", "op_power_keepalive_lost",
            "op_headset_disconnected", "op_env_dark", "op_env_bright"
        )
        val languages = listOf("en", "hi", "ta", "kn", "ml", "te")
        for (key in keys) {
            for (language in languages) {
                val spoken = AlertManager.operationalText(key, language)
                assertTrue("$key/$language must resolve", spoken.isNotBlank())
            }
        }
    }

    @Test
    fun unknownOperationalKeyIsNeverSpokenAsARawKey() {
        val key = "not_a_real_operational_key"
        val spoken = AlertManager.operationalText(key, "en")
        assertTrue(spoken.isEmpty())
        assertTrue(spoken != key)
    }

    @Test
    fun lookFailedAndBusyRoadAreLocalizedBeyondHindi() {
        for (language in listOf("ta", "kn", "ml", "te")) {
            val look = AlertManager.operationalText("op_look_unavailable", language)
            assertTrue("op_look_unavailable/$language must be non-empty", look.isNotEmpty())
        }
    }
}
