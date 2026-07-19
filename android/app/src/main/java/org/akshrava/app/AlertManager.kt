package org.akshrava.app

import android.content.Context
import android.media.AudioAttributes
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.ArrayDeque
import java.util.Locale

/**
 * Owns speech, haptics and the "never drown the user" rate policy (§6.3).
 * Hard rules enforced here: at most one utterance per 2 s, an S1 alert preempts,
 * the same object re-alerts only after its cooldown, and three alerts in ten seconds
 * collapse to a single "busy road" summary until the scene calms.
 */
class AlertManager(private val context: Context, languageTag: String) : TextToSpeech.OnInitListener {
    private companion object {
        const val OBJECT_COOLDOWN_MS = 5_000L
        const val MIN_UTTERANCE_GAP_MS = 2_000L
        const val BUSY_WINDOW_MS = 10_000L
        const val BUSY_COUNT = 3
        const val SUMMARY_COOLDOWN_MS = 5_000L
    }

    private var tts: TextToSpeech? = TextToSpeech(context, this)
    private val vibrator = context.getSystemService(Vibrator::class.java)
    private val language = Locale.forLanguageTag(languageTag)
    private val isHindi = language.language == "hi"
    private val lastSpoken = mutableMapOf<String, Long>()
    private val recentUtterances = ArrayDeque<Long>()
    private var lastUtteranceMs = 0L
    private var lastSummaryMs = 0L
    private data class PendingStatus(val text: String, val onComplete: (() -> Unit)?)

    private val completionLock = Any()
    private val completionCallbacks = mutableMapOf<String, () -> Unit>()
    private var pendingStatus: PendingStatus? = null
    private var statusSequence = 0L
    @Volatile private var ready = false

    override fun onInit(status: Int) {
        ready = status == TextToSpeech.SUCCESS
        if (ready) {
            tts?.language = language
            tts?.setAudioAttributes(
                AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY).build()
            )
            tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String) = Unit
                override fun onDone(utteranceId: String) = complete(utteranceId)
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String) = complete(utteranceId)
                override fun onError(utteranceId: String, errorCode: Int) = complete(utteranceId)
            })
            pendingStatus?.let {
                pendingStatus = null
                speak(it.text, flush = true, id = nextStatusId(), onComplete = it.onComplete)
            }
        } else {
            pendingStatus?.onComplete?.invoke()
            pendingStatus = null
        }
    }

    fun announce(messageKey: String, bearing: String, urgent: Boolean, haptic: String) {
        val now = SystemClock.elapsedRealtime()
        val cooldownKey = "$messageKey:$bearing"
        val previous = lastSpoken[cooldownKey]
        if (previous != null && now - previous < OBJECT_COOLDOWN_MS) return

        pruneRecent(now)
        if (!urgent) {
            if (now - lastUtteranceMs < MIN_UTTERANCE_GAP_MS) return
            if (recentUtterances.size >= BUSY_COUNT) {
                summarize(now)
                return
            }
        }

        lastSpoken[cooldownKey] = now
        markUtterance(now)
        vibrate(haptic)
        
        val text = template(messageKey, bearing)
        // S1 cuts the current utterance mid-word; interruption itself signals urgency.
        speak(text, flush = urgent, id = cooldownKey)
    }

    fun status(text: String, onComplete: (() -> Unit)? = null) {
        if (ready) {
            speak(text, flush = true, id = nextStatusId(), onComplete = onComplete)
        } else {
            pendingStatus?.onComplete?.invoke()
            pendingStatus = PendingStatus(text, onComplete)
        }
    }

    private fun summarize(now: Long) {
        if (now - lastSummaryMs < SUMMARY_COOLDOWN_MS) return
        lastSummaryMs = now
        markUtterance(now)
        val text = if (isHindi) "व्यस्त रास्ता, ध्यान से" else "Busy road, careful"
        vibrate("single")
        speak(text, flush = true, id = "summary")
    }

    private fun pruneRecent(now: Long) {
        while (recentUtterances.isNotEmpty() && now - recentUtterances.peekFirst() > BUSY_WINDOW_MS) {
            recentUtterances.pollFirst()
        }
    }

    private fun markUtterance(now: Long) {
        lastUtteranceMs = now
        recentUtterances.addLast(now)
    }

    private fun speak(text: String, flush: Boolean, id: String, onComplete: (() -> Unit)? = null) {
        if (ready) {
            if (onComplete != null) synchronized(completionLock) { completionCallbacks[id] = onComplete }
            val result = tts?.speak(
                text, if (flush) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, null, id
            ) ?: TextToSpeech.ERROR
            if (result == TextToSpeech.ERROR) complete(id)
        } else {
            onComplete?.invoke()
        }
    }

    private fun nextStatusId(): String = "status-${++statusSequence}"

    private fun complete(utteranceId: String) {
        val callback = synchronized(completionLock) { completionCallbacks.remove(utteranceId) }
        callback?.invoke()
    }

    private fun template(key: String, bearing: String): String = when (key) {
        "obstacle_ahead" -> if (isHindi) "आगे रुकावट" else "Obstacle ahead"
        "vehicle_nearby" -> if (isHindi) {
            when (bearing) { "left" -> "वाहन बाईं ओर है"; "right" -> "वाहन दाईं ओर है"; else -> "वाहन आगे है" }
        } else "Vehicle nearby, $bearing"
        else -> if (isHindi) "सहायता सीमित है" else "Assistance is limited"
    }

    private fun vibrate(pattern: String) {
        val timings = when (pattern) {
            "single" -> longArrayOf(0, 80)
            "double" -> longArrayOf(0, 60, 90, 60)
            "triple" -> longArrayOf(0, 60, 70, 60, 70, 60)
            else -> return
        }
        vibrator?.vibrate(VibrationEffect.createWaveform(timings, -1))
    }

    fun shutdown() {
        val pending = synchronized(completionLock) {
            val values = completionCallbacks.values.toMutableList()
            completionCallbacks.clear()
            values
        }
        pendingStatus?.onComplete?.let { pending += it }
        pendingStatus = null
        tts?.shutdown()
        tts = null
        pending.forEach { it.invoke() }
    }
}
