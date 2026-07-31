package org.akshrava.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.support.v4.media.session.MediaSessionCompat
import android.support.v4.media.session.PlaybackStateCompat
import android.view.KeyEvent

/** Headset button via MediaSession: single=repeat, double=mute 15m, long-press=look. */
class HeadsetControls(
    context: Context,
    private val onRepeat: () -> Unit,
    private val onMute: () -> Unit,
    private val onLook: () -> Unit,
    /**
     * Audio is about to fall back to the phone speaker (earbuds died, cable pulled) — F-17.
     *
     * Deliberately NOT wired to [onMute]. A media player pauses on this broadcast because the
     * only cost is embarrassment; here the "media" is the hazard channel, and going quiet
     * because a cable came loose strands the user with no awareness and no indication that
     * anything changed. The route change is announced instead, and alerts keep flowing.
     */
    private val onAudioRouteLost: () -> Unit = {}
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
    private val appContext = context.applicationContext
    private var noisyReceiverRegistered = false
    private val noisyReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            if (intent?.action == AudioManager.ACTION_AUDIO_BECOMING_NOISY) {
                onAudioRouteLost()
            }
        }
    }

    fun start() {
        registerNoisyReceiver()
        session.setCallback(object : MediaSessionCompat.Callback() {
            override fun onMediaButtonEvent(mediaButtonEvent: Intent?): Boolean {
                val key = mediaButtonEvent.keyEvent() ?: return false
                if (key.action == KeyEvent.ACTION_DOWN && key.repeatCount == 0) {
                    handler.removeCallbacksAndMessages(null)
                    val generation = ++pressGeneration
                    handler.postDelayed({
                        if (pressGeneration == generation) {
                            pressCount = 0
                            onLook()
                        }
                    }, 700)
                    return true
                }
                
                if (key.action != KeyEvent.ACTION_UP) return true
                handler.removeCallbacksAndMessages(null)
                
                if (key.eventTime - key.downTime >= 700) {
                    // A long-press look already pre-empted and fired.
                    return true
                }
                
                val now = SystemClock.elapsedRealtime()
                if (now - lastPressMs < 400) pressCount += 1 else pressCount = 1
                lastPressMs = now
                
                if (pressCount >= 2) {
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

    /**
     * A failure here must not take the media buttons down with it: repeat / mute / look are the
     * user's only manual controls, and registering the route-change receiver first meant one
     * throwing OEM ROM disabled all three.
     */
    private fun registerNoisyReceiver() {
        if (noisyReceiverRegistered) return
        val filter = IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY)
        runCatching {
            if (Build.VERSION.SDK_INT >= 33) {
                appContext.registerReceiver(noisyReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
            } else {
                appContext.registerReceiver(noisyReceiver, filter)
            }
        }.onSuccess {
            noisyReceiverRegistered = true
        }.onFailure {
            Log.w("AkshravaDebug", "headset_noisy_register_failed", it)
        }
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
        if (noisyReceiverRegistered) {
            noisyReceiverRegistered = false
            runCatching { appContext.unregisterReceiver(noisyReceiver) }
                .onFailure { Log.w("AkshravaDebug", "headset_noisy_unregister_failed", it) }
        }
        handler.removeCallbacksAndMessages(null)
        session.isActive = false
        session.release()
    }
}