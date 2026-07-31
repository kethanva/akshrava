package org.akshrava.app

import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager

/**
 * Transient ducking focus for assistive TTS (F-09).
 *
 * TalkBack and background media must yield briefly while an alert speaks, then resume.
 * Uses [AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK] so other players duck rather than
 * pause hard — less disorienting mid-walk on donated phones (API 26+).
 */
object AlertAudioFocus {
    const val FOCUS_GAIN = AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK

    fun shouldRequest(ready: Boolean, closed: Boolean): Boolean = ready && !closed

    fun buildRequest(listener: AudioManager.OnAudioFocusChangeListener): AudioFocusRequest {
        val attrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        return AudioFocusRequest.Builder(FOCUS_GAIN)
            .setAudioAttributes(attrs)
            .setOnAudioFocusChangeListener(listener)
            .setAcceptsDelayedFocusGain(false)
            .build()
    }

    fun request(audioManager: AudioManager?, request: AudioFocusRequest): Boolean {
        if (audioManager == null) return false
        val result = audioManager.requestAudioFocus(request)
        return result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    fun abandon(audioManager: AudioManager?, request: AudioFocusRequest) {
        if (audioManager == null) return
        runCatching { audioManager.abandonAudioFocusRequest(request) }
    }
}
