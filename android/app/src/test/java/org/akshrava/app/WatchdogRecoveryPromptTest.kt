package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The watchdog prompt is the last channel left when assistance has already stopped: the user
 * cannot see the notification, so the spoken line is the recovery instruction.
 */
class WatchdogRecoveryPromptTest {
    @Test
    fun recoveryPromptSpeaksTheUsersOwnProvisionedLanguage() {
        // AppConfig stores BCP-47 tags. Locale("hi-IN") makes "hi-in" the *language*, which is not
        // an ISO code, so TextToSpeech.setLanguage reports LANG_NOT_SUPPORTED and falls back to the
        // engine default — a Hindi-only user hears an English instruction they cannot act on.
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
        // goAsync() gives a foreground broadcast about 10 s before the system declares a receiver
        // ANR. The TTS engine this prompt uses may be the one an OEM ROM just force-stopped, so a
        // missing utterance callback must resolve on our own timer, with margin.
        assertTrue(
            "speech timeout (${WatchdogReceiver.SPEECH_TIMEOUT_MS} ms) must finish inside the " +
                "10 s foreground broadcast budget",
            WatchdogReceiver.SPEECH_TIMEOUT_MS <= 8_000L
        )
    }
}
