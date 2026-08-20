package org.akshrava.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import androidx.core.app.NotificationCompat
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Fires from the watchdog alarm. If a session is meant to be active but the camera service has
 * gone silent, it raises a loud, high-priority prompt. It never starts the service itself —
 * the user must visibly press Start, per platform rules.
 */
class WatchdogReceiver : BroadcastReceiver() {
    internal companion object {
        const val CHANNEL_ID = "assist-watchdog"
        const val NOTIFICATION_ID = 2001

        /**
         * Upper bound on how long the async broadcast may stay open waiting for TTS.
         *
         * A BroadcastReceiver that never finishes its goAsync() result is killed by the system
         * after ~10 s with a receiver ANR. The engine this prompt depends on is the same one an
         * OEM ROM may have just force-stopped, in which case no utterance callback ever arrives.
         */
        const val SPEECH_TIMEOUT_MS = 6_000L

        /**
         * Locale for the recovery utterance.
         *
         * AppConfig stores BCP-47 tags ("hi-IN"); `Locale("hi-IN")` builds a locale whose
         * *language* is the whole string ("hi-in"), which is not an ISO code, so setLanguage
         * returns LANG_NOT_SUPPORTED and the prompt falls back to the engine default. A
         * Hindi-only user then hears an English instruction they cannot act on, at exactly the
         * moment assistance has already stopped.
         */
        fun speechLocale(languageTag: String): Locale =
            Locale.forLanguageTag(languageTag.trim().ifEmpty { "en-IN" })
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (!SessionFlags.isActive(context)) return
        val pendingResult = goAsync()
        if (SessionFlags.isStale(context)) {
            promptRestart(context, pendingResult)
        } else {
            pendingResult?.finish()
        }
        // Keep watching for as long as the session is meant to be active.
        Watchdog.schedule(context)
    }

    private fun promptRestart(context: Context, pendingResult: PendingResult?) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (manager == null) {
            pendingResult?.finish()
            return
        }
        val channel = NotificationChannel(
            CHANNEL_ID, context.getString(R.string.watchdog_channel_name), NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = context.getString(R.string.watchdog_channel_desc)
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 400, 200, 400)
        }
        manager.createNotificationChannel(channel)

        val open = android.app.PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle(context.getString(R.string.watchdog_title))
            .setContentText(context.getString(R.string.watchdog_text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setAutoCancel(true)
            .setContentIntent(open)
            .build()
        manager.notify(NOTIFICATION_ID, notification)
        // Short BLV recovery utterance; watchdog remains prompt-only for restart (never auto-starts).
        // The notification above is already posted, so every path below only decides whether the
        // prompt is also *spoken* — none of them may leave the broadcast unfinished.
        var ttsInstance: android.speech.tts.TextToSpeech? = null
        val settled = AtomicBoolean(false)
        val finish = {
            if (settled.compareAndSet(false, true)) {
                runCatching { ttsInstance?.shutdown() }
                pendingResult?.finish()
            }
        }
        // Backstop for an engine that accepts the utterance and then never reports it done —
        // observed on OEM ROMs that restart the TTS service underneath us. Without it the
        // receiver is killed by the system instead of finishing on its own terms.
        val timeout = Handler(Looper.getMainLooper())
        timeout.postDelayed({ finish() }, SPEECH_TIMEOUT_MS)
        ttsInstance = android.speech.tts.TextToSpeech(context) { status ->
            val engine = ttsInstance
            if (status != android.speech.tts.TextToSpeech.SUCCESS || engine == null) {
                timeout.removeCallbacksAndMessages(null)
                finish()
                return@TextToSpeech
            }
            engine.language = speechLocale(AppConfigStore.load(context).language)
            engine.setOnUtteranceProgressListener(object : android.speech.tts.UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = Unit
                override fun onDone(utteranceId: String?) {
                    timeout.removeCallbacksAndMessages(null)
                    finish()
                }
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) {
                    timeout.removeCallbacksAndMessages(null)
                    finish()
                }
            })
            val queued = engine.speak(
                context.getString(R.string.watchdog_text),
                android.speech.tts.TextToSpeech.QUEUE_FLUSH,
                null,
                "watchdog-recovery"
            )
            if (queued != android.speech.tts.TextToSpeech.SUCCESS) {
                // The engine initialised but refused the utterance (the "not bound to TTS engine"
                // state an OEM force-stop leaves behind). No progress callback will ever come,
                // so finish now rather than holding the broadcast open until the timeout.
                timeout.removeCallbacksAndMessages(null)
                finish()
            }
        }
    }
}
