package org.akshrava.app

import android.content.Context
import android.content.Intent
import android.os.Build
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
    private var lastPressMs = 0L
    private var pressCount = 0

    fun start() {
        session.setCallback(object : MediaSessionCompat.Callback() {
            override fun onMediaButtonEvent(mediaButtonEvent: Intent?): Boolean {
                val key = mediaButtonEvent.keyEvent() ?: return false
                if (key.action != KeyEvent.ACTION_UP) return true
                if (key.eventTime - key.downTime > 700) {
                    onLook(); return true
                }
                val now = SystemClock.elapsedRealtime()
                if (now - lastPressMs < 400) pressCount += 1 else pressCount = 1
                lastPressMs = now
                if (pressCount >= 2) {
                    pressCount = 0
                    onMute()
                } else {
                    android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                        if (pressCount == 1) {
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
        session.isActive = false
        session.release()
    }
}
