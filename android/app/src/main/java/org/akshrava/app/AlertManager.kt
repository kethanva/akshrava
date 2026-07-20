package org.akshrava.app

import android.content.Context
import android.media.AudioAttributes
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.ArrayDeque
import java.util.Locale
import java.util.concurrent.Executors

/**
 * Owns speech, haptics and the "never drown the user" rate policy (§6.3).
 * Hard rules enforced here: at most one utterance per 2 s, an S1 alert preempts,
 * the same object re-alerts only after its cooldown, and three alerts in ten seconds
 * collapse to a single "busy road" summary until the scene calms.
 *
 * Public methods are serialized onto a single worker so cooldown maps and the TTS queue
 * stay consistent across camera, headset, and main-thread callers.
 */
class AlertManager(private val context: Context, languageTag: String) : TextToSpeech.OnInitListener {
    private companion object {
        const val OBJECT_COOLDOWN_MS = 5_000L
        const val MIN_UTTERANCE_GAP_MS = 2_000L
        const val BUSY_WINDOW_MS = 10_000L
        const val BUSY_COUNT = 3
        const val SUMMARY_COOLDOWN_MS = 5_000L
        const val REPEATABLE_WINDOW_MS = 30_000L
        // An urgent phrase's comprehension-critical head must land: a second urgent alert may
        // queue behind it but must not cut it off within its first 350 ms (architecture §5).
        const val URGENT_PROTECT_MS = 350L
    }

    private val api = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = TextToSpeech(context, this)
    private val vibrator = context.getSystemService(Vibrator::class.java)
    private val language = Locale.forLanguageTag(languageTag)
    private val isHindi = language.language == "hi"
    private val lastSpoken = mutableMapOf<String, Long>()
    private val recentUtterances = ArrayDeque<Long>()
    private var lastUtteranceMs = 0L
    private var lastSummaryMs = 0L
    @Volatile private var mutedUntilMs = 0L
    @Volatile private var lastAlertText: String? = null
    @Volatile private var lastAlertAtMs = 0L
    private var lastUrgentSpokenAtMs = 0L
    private data class PendingStatus(val text: String, val onComplete: (() -> Unit)?)

    private val completionLock = Any()
    private val completionCallbacks = mutableMapOf<String, () -> Unit>()
    private var pendingStatus: PendingStatus? = null
    private var statusSequence = 0L
    @Volatile private var ready = false

    override fun onInit(status: Int) {
        api.execute {
            ready = status == TextToSpeech.SUCCESS
            if (ready) {
                tts?.language = language
                tts?.setAudioAttributes(
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY).build()
                )
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String) = Unit
                    override fun onDone(utteranceId: String) = complete(utteranceId)
                    override fun onStop(utteranceId: String, interrupted: Boolean) = complete(utteranceId)
                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String) = complete(utteranceId)
                    override fun onError(utteranceId: String, errorCode: Int) = complete(utteranceId)
                })
                // pendingStatus is written from status() on the camera analyzer's background
                // executor thread and read here on whatever thread the TTS engine completes init
                // on -- not necessarily the same thread. Guard it with the same lock already used
                // for completionCallbacks rather than relying on @Volatile visibility alone, since
                // this is a read-then-clear that must not race a concurrent status() write.
                val toSpeak = synchronized(completionLock) { pendingStatus.also { pendingStatus = null } }
                toSpeak?.let { speak(it.text, flush = true, id = nextStatusId(), onComplete = it.onComplete) }
            } else {
                val toDrop = synchronized(completionLock) { pendingStatus.also { pendingStatus = null } }
                toDrop?.onComplete?.invoke()
            }
        }
    }

    fun announce(messageKey: String, bearing: String, urgent: Boolean, haptic: String) {
        api.execute { announceLocked(messageKey, bearing, urgent, haptic) }
    }

    private fun announceLocked(messageKey: String, bearing: String, urgent: Boolean, haptic: String) {
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
        val text = template(messageKey, bearing)
        lastAlertText = text
        lastAlertAtMs = now
        // Muting silences speech, per the user's explicit request, but never haptics: the S1
        // buzz needs no words and is exactly the channel a muted user still relies on (§6.4).
        vibrate(haptic)
        if (now < mutedUntilMs) return
        // S1 cuts a CAUTION utterance mid-word; interruption itself signals urgency. But an
        // urgent phrase's own first 350 ms is protected: a second urgent alert queues behind it
        // instead of flushing, so the head of the first warning is always comprehensible.
        val protectingUrgentHead = urgent && now - lastUrgentSpokenAtMs < URGENT_PROTECT_MS
        if (urgent) lastUrgentSpokenAtMs = now
        speak(text, flush = urgent && !protectingUrgentHead, id = cooldownKey)
    }

    /** Double-press headset mute. Auto-unmutes after [durationMs] so it can never be left silently dead. */
    fun muteFor(durationMs: Long) {
        api.execute {
            mutedUntilMs = SystemClock.elapsedRealtime() + durationMs
            speak(
                if (isHindi) "पंद्रह मिनट के लिए म्यूट" else "Muted for fifteen minutes",
                flush = true, id = nextStatusId()
            )
        }
    }

    /** Single-press headset repeat: replays the last spoken alert if it is still recent. */
    fun repeatLast() {
        api.execute {
            val text = lastAlertText
            val now = SystemClock.elapsedRealtime()
            if (text == null || now - lastAlertAtMs > REPEATABLE_WINDOW_MS) {
                speak(
                    if (isHindi) "कोई हाल का अलर्ट नहीं" else "No recent alert",
                    flush = true, id = nextStatusId()
                )
                return@execute
            }
            speak(text, flush = true, id = nextStatusId())
        }
    }

    fun status(text: String, onComplete: (() -> Unit)? = null) {
        api.execute {
            if (ready) {
                speak(text, flush = true, id = nextStatusId(), onComplete = onComplete)
            } else {
                val replaced = synchronized(completionLock) {
                    pendingStatus.also { pendingStatus = PendingStatus(text, onComplete) }
                }
                replaced?.onComplete?.invoke()
            }
        }
    }

    /** Speak then run [onDone] (camera-failure teardown, etc.). */
    fun speakThen(text: String, utteranceId: String = "speak_then", onDone: () -> Unit) {
        status(text, onComplete = onDone)
    }

    /** User-pulled look summary: flush and bypass object cooldown / busy collapse. */
    fun speakComposed(text: String, urgent: Boolean = true) {
        api.execute {
            val now = SystemClock.elapsedRealtime()
            markUtterance(now)
            speak(text, flush = urgent, id = "look-${++statusSequence}")
        }
    }

    /** Every control action must be confirmed by voice (§6.4): an explicit look that never got
     * an answer -- send failure, or no priority result within its timeout -- must not resolve
     * into silence just because nothing came back. */
    fun announceLookFailed() {
        speakComposed(if (isHindi) "लुक विफल, फिर कोशिश करें" else "Look failed, try again")
    }

    /** Immediate confirmation that a long-press was registered, independent of network state --
     * the answer (or failure) may take up to LOOK_TIMEOUT_MS to arrive. */
    fun acknowledgeLook() {
        vibrate("single")
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
        while (recentUtterances.isNotEmpty() && now - recentUtterances.peekFirst()!! > BUSY_WINDOW_MS) {
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
        callback?.let { mainHandler.post(it) }
    }

    private fun template(key: String, bearing: String): String = when (key) {
        "obstacle_ahead" -> if (isHindi) "आगे रुकावट" else "Obstacle ahead"
        "vehicle_nearby" -> if (isHindi) {
            when (bearing) { "left" -> "वाहन बाईं ओर है"; "right" -> "वाहन दाईं ओर है"; else -> "वाहन आगे है" }
        } else "Vehicle nearby, $bearing"
        "busy_road" -> if (isHindi) "व्यस्त सड़क, सावधान" else "Busy road, careful"
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
            pendingStatus?.onComplete?.let { values += it }
            pendingStatus = null
            values
        }
        tts?.stop()
        tts?.shutdown()
        tts = null
        ready = false
        api.shutdownNow()
        pending.forEach { mainHandler.post(it) }
    }
}
