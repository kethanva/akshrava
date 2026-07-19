package org.akshrava.app

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import android.view.KeyEvent

/** Headset button via MediaSession: single=repeat, double=mute 15m, long-press=look. */
class HeadsetControls(
    context: Context,
    private val onRepeat: () -> Unit,
    private val onMute: () -> Unit,
    private val onLook: () -> Unit
) {
    private val session = MediaSessionCompat(context, "akshrava")
    private val handler = Handler(Looper.getMainLooper())
    private var lastPressMs = 0L
    private var pressCount = 0
    // Identifies one single-press debounce cycle. A long-press (look) bumps this so a repeat
    // callback scheduled by an earlier short press can never fire after an intervening look --
    // previously it only checked `pressCount == 1`, which a later unrelated single press could
    // innocently restore, letting a stale callback fire a spurious repeat.
    private var pressGeneration = 0

    fun start() {
        session.setCallback(object : MediaSessionCompat.Callback() {
            override fun onMediaButtonEvent(mediaButtonEvent: Intent?): Boolean {
                val key = mediaButtonEvent.keyEvent() ?: return false
                if (key.action != KeyEvent.ACTION_UP) return true
                if (key.eventTime - key.downTime > 700) {
                    // A long-press look pre-empts any pending single/double-press debounce from
                    // an immediately preceding short press.
                    handler.removeCallbacksAndMessages(null)
                    pressCount = 0
                    pressGeneration += 1
                    onLook()
                    return true
                }
                val now = SystemClock.elapsedRealtime()
                if (now - lastPressMs < 400) pressCount += 1 else pressCount = 1
                lastPressMs = now
                if (pressCount >= 2) {
                    handler.removeCallbacksAndMessages(null)
                    pressCount = 0
                    pressGeneration += 1
                    onMute()
                } else {
                    val generation = ++pressGeneration
                    handler.postDelayed({
                        if (pressGeneration == generation && pressCount == 1) {
                            pressCount = 0
                            onRepeat()
                        }
                    }, 420)
                }
                return true
            }
        })
        session.setPlaybackState(
            PlaybackStateCompat.Builder()
                .setActions(PlaybackStateCompat.ACTION_PLAY or PlaybackStateCompat.ACTION_PAUSE)
                .setState(PlaybackStateCompat.STATE_PLAYING, 0, 1f)
                .build()
        )
        session.isActive = true
    }

    private fun Intent?.keyEvent(): KeyEvent? {
        if (this == null) return null
        return if (Build.VERSION.SDK_INT >= 33) {
            getParcelableExtra(Intent.EXTRA_KEY_EVENT, KeyEvent::class.java)
        } else {
            @Suppress("DEPRECATION")
            getParcelableExtra(Intent.EXTRA_KEY_EVENT)
        }
    }

    fun stop() {
        handler.removeCallbacksAndMessages(null)
        session.isActive = false
        session.release()
    }
}
