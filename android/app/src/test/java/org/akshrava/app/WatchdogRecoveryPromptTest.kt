package org.akshrava.app

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.ArgumentMatchers.anyInt
import org.mockito.ArgumentMatchers.anyString
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class WatchdogRecoveryPromptTest {
    @Test
    fun recoveryPromptSpeaksTheUsersOwnProvisionedLanguage() {
        for (supported in SupportedLanguages.all) {
            val locale = WatchdogReceiver.speechLocale(supported.tag)
            assertEquals(
                "${supported.tag} must resolve to its ISO language code",
                supported.wireCode,
                locale.language
            )
        }
    }

    @Test
    fun blankProvisionedLanguageStillYieldsASpeakableLocale() {
        assertEquals("en", WatchdogReceiver.speechLocale("").language)
        assertEquals("en", WatchdogReceiver.speechLocale("   ").language)
    }

    @Test
    fun speechTimeoutFinishesTheBroadcastBeforeTheSystemKillsIt() {
        assertTrue(
            "speech timeout (${WatchdogReceiver.SPEECH_TIMEOUT_MS} ms) must finish inside the " +
                "10 s foreground broadcast budget",
            WatchdogReceiver.SPEECH_TIMEOUT_MS <= 8_000L
        )
    }

    @Test
    fun onReceiveWhenSessionInactiveExitsEarly() {
        val receiver = WatchdogReceiver()
        val mockContext = mock(Context::class.java)
        val mockPrefs = mock(SharedPreferences::class.java)
        `when`(mockContext.getSharedPreferences(anyString(), anyInt())).thenReturn(mockPrefs)
        `when`(mockPrefs.getBoolean("session_active", false)).thenReturn(false)

        receiver.onReceive(mockContext, Intent())
    }
}
