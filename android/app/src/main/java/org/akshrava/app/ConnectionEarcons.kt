package org.akshrava.app

import android.media.AudioManager
import android.media.ToneGenerator

/**
 * Short connection-state earcons (F-07) for noisy streets where a full TTS sentence is slow.
 *
 * Uses [ToneGenerator] on the accessibility stream so no raw assets are required and API 26
 * recycled phones work without MediaPlayer latency. Callers must [release] on session teardown.
 */
class ConnectionEarcons(
    private val tones: ToneGenerator? = runCatching {
        ToneGenerator(AudioManager.STREAM_ACCESSIBILITY, VOLUME)
    }.getOrNull()
) {
    companion object {
        const val VOLUME = 70
        const val CONNECTED_MS = 100
        const val DROPPED_MS = 150
        const val RESTORED_MS = 120
        const val STALE_TICK_MS = 50
        const val LOOK_FAILED_MS = 50
    }

    fun connected() {
        runCatching { tones?.startTone(ToneGenerator.TONE_PROP_PROMPT, CONNECTED_MS) }
    }

    fun dropped() {
        runCatching { tones?.startTone(ToneGenerator.TONE_PROP_NACK, DROPPED_MS) }
    }

    fun restored() {
        runCatching { tones?.startTone(ToneGenerator.TONE_PROP_ACK, RESTORED_MS) }
    }

    fun staleTick() {
        runCatching { tones?.startTone(ToneGenerator.TONE_PROP_BEEP, STALE_TICK_MS) }
    }

    fun lookFailed() {
        runCatching { tones?.startTone(ToneGenerator.TONE_CDMA_ABBR_ALERT, LOOK_FAILED_MS) }
    }

    /** Soft chirp while a reconnect is scheduled after a drop. */
    fun reconnectPending() {
        runCatching { tones?.startTone(ToneGenerator.TONE_CDMA_ABBR_ALERT, LOOK_FAILED_MS) }
    }

    fun release() {
        runCatching { tones?.release() }
    }
}
