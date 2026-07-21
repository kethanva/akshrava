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
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/**
 * Owns speech, haptics and the "never drown the user" rate policy (§6.3).
 * Hard rules enforced here: at most one utterance per 2 s, an S1 alert preempts,
 * the same object re-alerts only after its cooldown, and three alerts in ten seconds
 * collapse to a single "busy road" summary until the scene calms.
 *
 * Public methods are serialized onto a single worker so cooldown maps and the TTS queue
 * stay consistent across camera, headset, and main-thread callers.
 *
 * The phone owns the 5 s object cooldown. When a non-urgent alert arrives inside the 2 s
 * utterance gap it is deferred once (not dropped), so a server admit is not wasted as silence.
 */
class AlertManager(private val context: Context, languageTag: String) : TextToSpeech.OnInitListener {
    internal companion object {
        const val OBJECT_COOLDOWN_MS = 5_000L
        const val MIN_UTTERANCE_GAP_MS = 2_000L
        const val BUSY_WINDOW_MS = 10_000L
        const val BUSY_COUNT = 3
        const val SUMMARY_COOLDOWN_MS = 5_000L
        const val REPEATABLE_WINDOW_MS = 30_000L
        // An urgent phrase's comprehension-critical head must land: a second urgent alert may
        // queue behind it but must not cut it off within its first 350 ms (architecture §5).
        const val URGENT_PROTECT_MS = 350L
        /**
         * TTS engine rebind policy. Force-stopping com.google.android.tts logs "Disconnected from TTS engine" and every later
         * tts.speak() returns ERROR ("speak failed: not bound to TTS engine") — the framework
         * detection keeps working, exactly the reported "works a few minutes then goes silent
         * until Stop/Start" failure. Aggressive OEM ROMs kill the engine app's process a few
         * minutes after the screen locks, which is how this fires mid-walk. On a failed speak we
         * rebuild the TextToSpeech client (which restarts the engine service); the streak bound
         * and interval floor keep a genuinely broken engine from becoming a rebuild loop, and a
         * successful hand-off to the engine resets the streak so recovery works repeatedly over
         * a long session.
         */
        const val ENGINE_REBUILD_MAX_STREAK = 4
        const val ENGINE_REBUILD_MIN_INTERVAL_MS = 4_000L

        /** Remaining wait before a gap-blocked caution may speak; null if it may speak now. */
        fun deferralDelayMs(nowMs: Long, lastUtteranceMs: Long, gapMs: Long = MIN_UTTERANCE_GAP_MS): Long? {
            val elapsed = nowMs - lastUtteranceMs
            if (elapsed >= gapMs) return null
            return (gapMs - elapsed).coerceAtLeast(0L)
        }

        /** Pure gate for the engine-rebuild decision so the policy is unit-testable. */
        fun engineRebuildAllowed(nowMs: Long, lastRebuildMs: Long, streak: Int): Boolean =
            streak < ENGINE_REBUILD_MAX_STREAK &&
                (lastRebuildMs == 0L || nowMs - lastRebuildMs >= ENGINE_REBUILD_MIN_INTERVAL_MS)
    }

    private data class PendingStatus(val text: String, val onComplete: (() -> Unit)?)
    private data class PendingAnnounce(val messageKey: String, val bearing: String, val haptic: String)

    private val api = Executors.newSingleThreadScheduledExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val language = Locale.forLanguageTag(languageTag)
    private val languageCode = language.language.lowercase()
    private val isHindi = languageCode == "hi"
    // Must be initialized before TextToSpeech: older APIs (and some emulators) invoke
    // onInit synchronously from the TTS constructor.
    private val completionLock = Any()
    private val completionCallbacks = mutableMapOf<String, () -> Unit>()
    private var pendingStatus: PendingStatus? = null
    private var pendingAnnounce: PendingAnnounce? = null
    private var pendingAnnounceFuture: ScheduledFuture<*>? = null
    private var statusSequence = 0L
    @Volatile private var ready = false
    private val lastSpoken = mutableMapOf<String, Long>()
    private val recentUtterances = ArrayDeque<Long>()
    private var lastUtteranceMs = 0L
    private var lastSummaryMs = 0L
    @Volatile private var mutedUntilMs = 0L
    @Volatile private var lastAlertText: String? = null
    @Volatile private var lastAlertAtMs = 0L
    private var lastUrgentSpokenAtMs = 0L
    private val vibrator = context.getSystemService(Vibrator::class.java)
    @Volatile private var closed = false
    /** Consecutive engine rebuilds without a successful speak hand-off; see engineRebuildAllowed. */
    @Volatile private var engineRebuildStreak = 0
    @Volatile private var lastEngineRebuildMs = 0L
    private var tts: TextToSpeech? = TextToSpeech(context, this)

    override fun onInit(status: Int) {
        // TTS init is asynchronous on many OEMs. shutdown() may already have torn down the
        // worker; never throw RejectedExecutionException back onto the main thread.
        if (closed || api.isShutdown) return
        runCatching {
            api.execute {
                if (closed) return@execute
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
                } else if (engineRebuildAllowed(
                        SystemClock.elapsedRealtime() + ENGINE_REBUILD_MIN_INTERVAL_MS,
                        lastEngineRebuildMs,
                        engineRebuildStreak
                    )
                ) {
                    // Engine failed to initialise (it can be mid-restart right after an OEM
                    // kill). Retry on the same bounded budget as a dead-connection rebuild,
                    // keeping pendingStatus so the queued utterance survives into the retry.
                    runCatching {
                        api.schedule(
                            { rebuildEngine(null, null) },
                            ENGINE_REBUILD_MIN_INTERVAL_MS,
                            TimeUnit.MILLISECONDS
                        )
                    }
                } else {
                    val toDrop = synchronized(completionLock) { pendingStatus.also { pendingStatus = null } }
                    toDrop?.onComplete?.invoke()
                }
            }
        }
    }

    private fun runApi(block: () -> Unit) {
        if (closed || api.isShutdown) return
        runCatching { api.execute(block) }
    }

    fun announce(messageKey: String, bearing: String, urgent: Boolean, haptic: String) {
        runApi { announceLocked(messageKey, bearing, urgent, haptic) }
    }

    private fun announceLocked(messageKey: String, bearing: String, urgent: Boolean, haptic: String) {
        val now = SystemClock.elapsedRealtime()
        val cooldownKey = "$messageKey:$bearing"
        val previous = lastSpoken[cooldownKey]
        if (previous != null && now - previous < OBJECT_COOLDOWN_MS) {
            // #region agent log
            AgentDebugLog.log(
                "H6",
                "AlertManager.announceLocked:cooldown",
                "announce_suppressed_cooldown",
                mapOf("messageKey" to messageKey, "bearing" to bearing, "urgent" to urgent)
            )
            // #endregion
            return
        }

        pruneRecent(now)
        if (!urgent) {
            val deferMs = deferralDelayMs(now, lastUtteranceMs)
            if (deferMs != null) {
                // #region agent log
                AgentDebugLog.log(
                    "H6",
                    "AlertManager.announceLocked:defer",
                    "announce_deferred",
                    mapOf("messageKey" to messageKey, "deferMs" to deferMs)
                )
                // #endregion
                scheduleDeferredCaution(PendingAnnounce(messageKey, bearing, haptic), deferMs)
                return
            }
            if (recentUtterances.size >= BUSY_COUNT) {
                cancelDeferredCaution()
                // #region agent log
                AgentDebugLog.log(
                    "H6",
                    "AlertManager.announceLocked:busy",
                    "announce_collapsed_busy",
                    mapOf("messageKey" to messageKey)
                )
                // #endregion
                summarize(now)
                return
            }
        } else {
            // Urgent preempts a waiting caution so the gap deferral cannot bury S1.
            cancelDeferredCaution()
        }

        // #region agent log
        AgentDebugLog.log(
            "H6",
            "AlertManager.announceLocked:deliver",
            "announce_delivered",
            mapOf("messageKey" to messageKey, "bearing" to bearing, "urgent" to urgent)
        )
        // #endregion
        deliverAnnounce(messageKey, bearing, urgent, haptic, now, cooldownKey)
    }

    private fun deliverAnnounce(
        messageKey: String,
        bearing: String,
        urgent: Boolean,
        haptic: String,
        now: Long,
        cooldownKey: String
    ) {
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

    private fun scheduleDeferredCaution(pending: PendingAnnounce, delayMs: Long) {
        pendingAnnounce = pending
        pendingAnnounceFuture?.cancel(false)
        pendingAnnounceFuture = api.schedule({
            val next = pendingAnnounce ?: return@schedule
            pendingAnnounce = null
            pendingAnnounceFuture = null
            announceLocked(next.messageKey, next.bearing, urgent = false, next.haptic)
        }, delayMs, TimeUnit.MILLISECONDS)
    }

    private fun cancelDeferredCaution() {
        pendingAnnounceFuture?.cancel(false)
        pendingAnnounceFuture = null
        pendingAnnounce = null
    }

    /** Double-press headset mute. Auto-unmutes after [durationMs] so it can never be left silently dead. */
    fun muteFor(durationMs: Long) {
        runApi {
            mutedUntilMs = SystemClock.elapsedRealtime() + durationMs
            speak(
                when (languageCode) {
                    "hi" -> "पंद्रह मिनट के लिए म्यूट"
                    "ta" -> "பதினைந்து நிமிடங்களுக்கு ஒலி நிறுத்தப்பட்டது"
                    "kn" -> "ಹದಿನೈದು ನಿಮಿಷಗಳ ಕಾಲ ಮ್ಯೂಟ್ ಮಾಡಲಾಗಿದೆ"
                    "ml" -> "പതിനഞ്ച് മിനിറ്റിലേക്ക് മ്യൂട്ട് ചെയ്തു"
                    "te" -> "పదిహేను నిమిషాల పాటు మ్యూట్ చేయబడింది"
                    else -> "Muted for fifteen minutes"
                },
                flush = true, id = nextStatusId()
            )
        }
    }

    /** Single-press headset repeat: replays the last spoken alert if it is still recent. */
    fun repeatLast() {
        runApi {
            val text = lastAlertText
            val now = SystemClock.elapsedRealtime()
            if (text == null || now - lastAlertAtMs > REPEATABLE_WINDOW_MS) {
                speak(
                    if (isHindi) "कोई हाल का अलर्ट नहीं" else "No recent alert",
                    flush = true, id = nextStatusId()
                )
                return@runApi
            }
            speak(text, flush = true, id = nextStatusId())
        }
    }

    fun status(text: String, onComplete: (() -> Unit)? = null) {
        runApi {
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
        runApi {
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
        if (!ready) {
            // Engine is (re)connecting. Keep the NEWEST utterance for delivery after init —
            // onInit speaks pendingStatus on success — instead of dropping it into silence.
            // For a blind user a late warning still beats no warning.
            val replaced = synchronized(completionLock) {
                pendingStatus.also { pendingStatus = PendingStatus(text, onComplete) }
            }
            replaced?.onComplete?.invoke()
            return
        }
        if (onComplete != null) synchronized(completionLock) { completionCallbacks[id] = onComplete }
        val result = tts?.speak(
            text, if (flush) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, null, id
        ) ?: TextToSpeech.ERROR
        if (result == TextToSpeech.SUCCESS) {
            // The engine accepted the utterance: the binding is alive, so recovery quota refills.
            engineRebuildStreak = 0
            return
        }
        // speak() returning ERROR after successful init means the engine connection is dead
        // (verified live: OEM force-stop of com.google.android.tts -> "speak failed: not bound
        // to TTS engine" on every call, forever). The framework never rebinds; we must.
        synchronized(completionLock) { completionCallbacks.remove(id) }
        AgentDebugLog.log(
            "H7", "AlertManager.speak:engineDead", "tts_speak_failed",
            mapOf("id" to id, "streak" to engineRebuildStreak)
        )
        rebuildEngine(text, onComplete)
    }

    /**
     * Tear down the dead TextToSpeech client and bind a fresh one, re-queueing [text] so the
     * failed utterance is spoken as soon as the new engine initialises. Bounded by
     * [engineRebuildAllowed]; when the quota is exhausted the callback still runs (teardown
     * paths depend on it) and haptics remain the surviving alert channel — deliverAnnounce
     * vibrates before speaking, so S1 buzzes continue even with speech gone.
     */
    private fun rebuildEngine(text: String?, onComplete: (() -> Unit)?) {
        if (closed) {
            onComplete?.invoke()
            return
        }
        ready = false
        if (text != null) {
            val replaced = synchronized(completionLock) {
                pendingStatus.also { pendingStatus = PendingStatus(text, onComplete) }
            }
            replaced?.onComplete?.invoke()
        }
        val now = SystemClock.elapsedRealtime()
        if (!engineRebuildAllowed(now, lastEngineRebuildMs, engineRebuildStreak)) return
        lastEngineRebuildMs = now
        engineRebuildStreak += 1
        AgentDebugLog.log(
            "H7", "AlertManager.rebuildEngine", "tts_engine_rebuild",
            mapOf("streak" to engineRebuildStreak)
        )
        runCatching { tts?.shutdown() }
        tts = null
        // Construct on the main thread like the original init so engine callbacks keep their
        // threading assumptions; shutdown() also runs on main, so closed=true is ordered
        // before this post executes and cannot leak a fresh engine binding after teardown.
        mainHandler.post {
            if (closed) return@post
            tts = TextToSpeech(context, this)
        }
    }

    private fun nextStatusId(): String = "status-${++statusSequence}"

    private fun complete(utteranceId: String) {
        val callback = synchronized(completionLock) { completionCallbacks.remove(utteranceId) }
        callback?.let { mainHandler.post(it) }
    }

    private fun template(key: String, bearing: String): String = when (key) {
        "obstacle_ahead" -> when (languageCode) {
            "hi" -> "आगे रुकावट"; "ta" -> "முன்னே தடையுள்ளது"; "kn" -> "ಮುಂದೆ ಅಡಚಣೆ ಇದೆ"
            "ml" -> "മുന്നിൽ തടസ്സമുണ്ട്"; "te" -> "ముందు అడ్డంకి ఉంది"; else -> "Obstacle ahead"
        }
        "person_ahead" -> when (languageCode) {
            "hi" -> "आगे व्यक्ति"; "ta" -> "முன்னே நபர் உள்ளார்"; "kn" -> "ಮುಂದೆ ವ್ಯಕ್ತಿ ಇದ್ದಾರೆ"
            "ml" -> "മുന്നിൽ വ്യക്തിയുണ്ട്"; "te" -> "ముందు వ్యక్తి ఉన్నారు"; else -> "Person ahead"
        }
        "vehicle_nearby" -> when (languageCode) {
            "hi" -> when (bearing) { "left" -> "वाहन बाईं ओर है"; "right" -> "वाहन दाईं ओर है"; else -> "वाहन आगे है" }
            "ta" -> "அருகில் வாகனம் ${bearingTa[bearing] ?: bearingTa["ahead"]}"
            "kn" -> "ಹತ್ತಿರ ವಾಹನ ${bearingKn[bearing] ?: bearingKn["ahead"]}"
            "ml" -> "അടുത്ത് വാഹനം ${bearingMl[bearing] ?: bearingMl["ahead"]}"
            "te" -> "సమీపంలో వాహనం ${bearingTe[bearing] ?: bearingTe["ahead"]}"
            else -> "Vehicle nearby, $bearing"
        }
        "busy_road" -> when (languageCode) {
            "hi" -> "व्यस्त सड़क, सावधान"; "ta" -> "பரபரப்பான சாலை, கவனம்"; "kn" -> "ಗಿಜಿಗುಡಿದ ರಸ್ತೆ, ಎಚ್ಚರಿಕೆ"
            "ml" -> "തിരക്കേറിയ റോഡ്, ശ്രദ്ധിക്കുക"; "te" -> "రద్దీగా ఉన్న రహదారి, జాగ్రత్త"; else -> "Busy road, careful"
        }
        else -> when (languageCode) {
            "hi" -> "सहायता सीमित है"; "ta" -> "உதவி வரம்புக்குட்பட்டது"; "kn" -> "ಸಹಾಯ ಸೀಮಿತವಾಗಿದೆ"
            "ml" -> "സഹായം പരിമിതമാണ്"; "te" -> "సహాయం పరిమితంగా ఉంది"; else -> "Assistance is limited"
        }
    }

    private val bearingTa = mapOf("left" to "இடப்புறம் உள்ளது", "right" to "வலப்புறம் உள்ளது", "ahead" to "முன்னே உள்ளது")
    private val bearingKn = mapOf("left" to "ಎಡಭಾಗದಲ್ಲಿದೆ", "right" to "ಬಲಭಾಗದಲ್ಲಿದೆ", "ahead" to "ಮುಂದೆ ಇದೆ")
    private val bearingMl = mapOf("left" to "ഇടതുവശത്തുണ്ട്", "right" to "വലതുവശത്തുണ്ട്", "ahead" to "മുന്നിലുണ്ട്")
    private val bearingTe = mapOf("left" to "ఎడమ వైపున ఉంది", "right" to "కుడి వైపున ఉంది", "ahead" to "ముందు ఉంది")

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
        closed = true
        cancelDeferredCaution()
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
