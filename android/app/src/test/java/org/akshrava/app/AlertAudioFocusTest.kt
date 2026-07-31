package org.akshrava.app

import android.media.AudioManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AlertAudioFocusTest {
    @Test
    fun duckingGainIsTransientMayDuck() {
        assertEquals(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK, AlertAudioFocus.FOCUS_GAIN)
    }

    @Test
    fun requestOnlyWhenEngineReadyAndSessionOpen() {
        assertTrue(AlertAudioFocus.shouldRequest(ready = true, closed = false))
        assertFalse(AlertAudioFocus.shouldRequest(ready = false, closed = false))
        assertFalse(AlertAudioFocus.shouldRequest(ready = true, closed = true))
    }

    @Test
    fun nestedHoldOnlyAbandonsOnLastRelease() {
        // Mirrors AlertManager focusHoldCount: acquire bumps; abandon only when back to zero.
        var holds = 0
        fun acquire(): Boolean {
            val previous = holds
            holds += 1
            return previous == 0
        }
        fun release(): Boolean {
            holds = (holds - 1).coerceAtLeast(0)
            return holds == 0
        }
        assertTrue(acquire())   // first speak requests system focus
        assertFalse(acquire())  // flush/queue nested speak — keep existing focus
        assertFalse(release())  // interrupted prior utterance must not abandon yet
        assertTrue(release())   // last utterance done — abandon
        assertEquals(0, holds)
    }
}
