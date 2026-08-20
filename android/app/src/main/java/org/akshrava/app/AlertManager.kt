package org.akshrava.app

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
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
        /** A headset mute is deliberately short and always announces its own expiry. */
        const val MUTE_AUTO_EXPIRE_MS = 120_000L

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

        /** Double-press toggles an active mute off; otherwise starts one bounded mute window. */
        fun nextMuteUntil(nowMs: Long, currentUntilMs: Long, durationMs: Long): Long =
            if (currentUntilMs > nowMs) 0L else nowMs + durationMs.coerceAtLeast(1L)

        fun speechSuppressedByMute(nowMs: Long, mutedUntilMs: Long, bypassMute: Boolean): Boolean =
            !bypassMute && nowMs < mutedUntilMs

        /**
         * Operational TTS keyed by stable ids, not English sentences. Unknown keys return empty
         * so a missing translation can never be spoken as a raw machine key.
         */
        fun operationalText(key: String, languageCode: String): String = when (key) {
            "op_connected" -> when (languageCode) {
                "hi" -> "दृष्टि सहायता जुड़ गई"; "ta" -> "பார்வை உதவி இணைந்தது"; "kn" -> "ದೃಷ್ಟಿ ಸಹಾಯ ಸಂಪರ್ಕಗೊಂಡಿದೆ"
                "ml" -> "കാഴ്ച സഹായം ബന്ധിപ്പിച്ചു"; "te" -> "దృష్టి సహాయం అనుసంధానమైంది"; else -> "Vision assistance connected"
            }
            "op_restored" -> when (languageCode) {
                "hi" -> "कनेक्शन बहाल हो गया"; "ta" -> "இணைப்பு மீண்டும் வந்தது"; "kn" -> "ಸಂಪರ್ಕ ಮರುಸ್ಥಾಪಿತವಾಗಿದೆ"
                "ml" -> "ബന്ധം വീണ്ടും ലഭിച്ചു"; "te" -> "కనెక్షన్ తిరిగి వచ్చింది"; else -> "Connection restored"
            }
            "op_link_lost" -> when (languageCode) {
                "hi" -> "दृष्टि सहायता उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।"
                "ta" -> "பார்வை உதவி கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ದೃಷ್ಟಿ ಸಹಾಯ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "കാഴ്ച സഹായം ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "దృష్టి సహాయం అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Vision assistance unavailable. Use cane or guide."
            }
            "op_vision_unavailable" -> when (languageCode) {
                "hi" -> "दृष्टि सहायता उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।"
                "ta" -> "பார்வை உதவி கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ದೃಷ್ಟಿ ಸಹಾಯ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "കാഴ്ച സഹായം ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "దృష్టి సహాయం అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Vision assistance unavailable. Use cane or guide."
            }
            "op_model_unavailable" -> when (languageCode) {
                "hi" -> "दृष्टि मॉडल उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।"
                "ta" -> "பார்வை மாதிரி கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ದೃಷ್ಟಿ ಮಾದರಿ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "കാഴ്ച മാതൃക ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "దృష్టి నమూనా అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Vision model unavailable. Use cane or guide."
            }
            "op_cloud_fallback_unavailable" -> when (languageCode) {
                "hi" -> "दूरस्थ दृष्टि विकल्प उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।"
                "ta" -> "மேகப் பார்வை மாற்று வழி கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಮೇಘ ದೃಷ್ಟಿ ಪರ್ಯಾಯ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "വിദൂര കാഴ്ച മറ്റൊരു വഴി ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "దూరపు దృష్టి ప్రత్యామ్నాయం అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Cloud vision fallback unavailable. Use cane or guide."
            }
            "op_server_shedding" -> when (languageCode) {
                "hi" -> "सर्वर व्यस्त है। बेंत या गाइड का उपयोग करें।"
                "ta" -> "சேவையகம் பணிமிகுதியில் உள்ளது. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಪೂರೈಕೆಗಣಕ ಕಾರ್ಯನಿರತ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "സെർവർ തിരക്കിലാണ്. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "సర్వర్ బిజీగా ఉంది. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Server busy. Use cane or guide."
            }
            "op_camera_dark" -> when (languageCode) {
                "hi" -> "कैमरा अँधेरा है। पीछे का लेंस खोलें।"
                "ta" -> "கேமரா இருட்டாக உள்ளது. பின்புற வில்லையைத் திறக்கவும்."
                "kn" -> "ಕ್ಯಾಮೆರಾ ಕತ್ತಲಾಗಿದೆ. ಹಿಂದಿನ ಮಸೂರವನ್ನು ತೆರೆಯಿರಿ."
                "ml" -> "ക്യാമറ ഇരുണ്ടതാണ്. പിന്നിലെ ലെൻസ് തുറന്ന് വയ്ക്കുക."
                "te" -> "కెమెరా చీకటిగా ఉంది. వెనుక లెన్స్‌ను తెరవండి."
                else -> "Camera is dark. Uncover the rear lens."
            }
            "op_camera_glare" -> when (languageCode) {
                "hi" -> "कैमरे पर तेज़ रोशनी है। थोड़ा मुड़ें।"
                "ta" -> "கேமரா ஒளியால் மறைந்துள்ளது. சற்று திரும்பவும்."
                "kn" -> "ಕ್ಯಾಮೆರಾ ಬೆಳಕಿನಿಂದ ಕುರುಡಾಗಿದೆ. ಸ್ವಲ್ಪ ತಿರುಗಿ."
                "ml" -> "ക്യാമറ വെളിച്ചത്താൽ തിളങ്ങുന്നു. അൽപ്പം തിരിയുക."
                "te" -> "కెమెరా వెలుతురుతో కప్పబడింది. కొద్దిగా తిరగండి."
                else -> "Camera blinded by light. Turn slightly."
            }
            "op_camera_blurry" -> when (languageCode) {
                "hi" -> "कैमरा धुँधला है। लेंस पोंछें। बेंत या गाइड का उपयोग करें।"
                "ta" -> "கேமரா மங்கலாக உள்ளது. வில்லையைத் துடைக்கவும். கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಕ್ಯಾಮೆರಾ ಮಬ್ಬಾಗಿದೆ. ಮಸೂರವನ್ನು ಒರೆಸಿ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "ക്യാമറ മങ്ങിയിരിക്കുന്നു. ലെൻസ് തുടയ്ക്കുക. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "కెమెరా మసకగా ఉంది. లెన్స్ తుడవండి. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Camera is blurry. Wipe the lens. Use cane or guide."
            }
            "op_camera_failed" -> when (languageCode) {
                "hi" -> "पीछे का कैमरा उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।"
                "ta" -> "பின்புற கேமரா கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಹಿಂದಿನ ಕ್ಯಾಮೆರಾ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "പിൻ ക്യാമറ ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "వెనుక కెమెరా అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Rear camera unavailable. Use cane or guide."
            }
            "op_camera_stalled" -> when (languageCode) {
                "hi" -> "कैमरा रुक गया। पुनर्प्राप्ति हो रही है।"
                "ta" -> "கேமரா நின்றது. மீட்டெடுக்கிறது."
                "kn" -> "ಕ್ಯಾಮೆರಾ ನಿಂತಿದೆ. ಮರುಪಡೆಯಲಾಗುತ್ತಿದೆ."
                "ml" -> "ക്യാമറ നിന്നു. വീണ്ടെടുക്കുന്നു."
                "te" -> "కెమెరా ఆగింది. పునరుద్ధరిస్తోంది."
                else -> "Camera stalled. Recovering."
            }
            "op_analyze_failed" -> when (languageCode) {
                "hi" -> "कैमरा प्रसंस्करण त्रुटि। बेंत या गाइड का उपयोग करें।"
                "ta" -> "கேமரா செயலாக்க பிழை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಕ್ಯಾಮೆರಾ ಸಂಸ್ಕರಣೆ ದೋಷ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "ക്യാമറ പ്രോസസ്സ് പിശക്. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "కెమెరా ప్రాసెసింగ్ లోపం. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Camera processing error. Use cane or guide."
            }
            "op_access_revoked" -> when (languageCode) {
                "hi" -> "डिवाइस की पहुँच रद्द कर दी गई है। स्वयंसेवक से इस फ़ोन को प्रावधान करवाएँ।"
                "ta" -> "சாதன அணுகல் ரத்து செய்யப்பட்டது. தன்னார்வலரிடம் இந்த தொலைபேசியை அமைக்கச் சொல்லவும்."
                "kn" -> "ಸಾಧನದ ಪ್ರವೇಶ ರದ್ದಾಗಿದೆ. ಸ್ವಯಂಸೇವಕರಿಂದ ಈ ದೂರವಾಣಿಯನ್ನು ಸಿದ್ಧಪಡಿಸಿ."
                "ml" -> "ഉപകരണ പ്രവേശനം റദ്ദാക്കി. സന്നദ്ധപ്രവർത്തകനോട് ഈ ഫോൺ ക്രമീകരിക്കാൻ പറയുക."
                "te" -> "పరికర ప్రవేశం రద్దు చేయబడింది. స్వచ్ఛంద సేవకునితో ఈ ఫోన్‌ను సిద్ధం చేయించండి."
                else -> "Device access has been revoked. Ask a volunteer to provision this phone."
            }
            "op_auth_failed" -> when (languageCode) {
                "hi" -> "डिवाइस प्रमाणीकरण विफल। स्वयंसेवक से नया टोकन प्रावधान करवाएँ।"
                "ta" -> "சாதன அங்கீகாரம் தோல்வியடைந்தது. தன்னார்வலரிடம் புதிய அடையாளத்தை அமைக்கச் சொல்லவும்."
                "kn" -> "ಸಾಧನ ದೃಢೀಕರಣ ವಿಫಲ. ಸ್ವಯಂಸೇವಕರಿಂದ ಹೊಸ ಚೀಟಿ ಸಿದ್ಧಪಡಿಸಿ."
                "ml" -> "ഉപകരണ ആധികാരികത പരാജയപ്പെട്ടു. സന്നദ്ധപ്രവർത്തകനോട് പുതിയ ടോക്കൺ ക്രമീകരിക്കാൻ പറയുക."
                "te" -> "పరికర ప్రామాణీకరణ విఫలమైంది. స్వచ్ఛంద సేవకునితో కొత్త టోకెన్ సిద్ధం చేయించండి."
                else -> "Device authentication failed. Ask a volunteer to provision a new token."
            }
            "op_session_taken_over" -> when (languageCode) {
                "hi" -> "सत्र दूसरे डिवाइस पर ले लिया गया। बेंत या गाइड का उपयोग करें।"
                "ta" -> "அமர்வு வேறு சாதனத்தில் எடுக்கப்பட்டது. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಅಧಿವೇಶನವನ್ನು ಬೇರೆ ಸಾಧನದಲ್ಲಿ ತೆಗೆದುಕೊಳ್ಳಲಾಗಿದೆ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "സെഷൻ മറ്റൊരു ഉപകരണത്തിൽ എടുത്തു. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "సెషన్ మరో పరికరంపై తీసుకోబడింది. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Session taken over on another device. Use cane or guide."
            }
            "op_look_unavailable" -> when (languageCode) {
                "hi" -> "लुक उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।"
                "ta" -> "பார்வை கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ನೋಟ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "നോട്ട് ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "చూపు అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Look unavailable. Use cane or guide."
            }
            "op_starting" -> when (languageCode) {
                "hi" -> "सहायता शुरू हो रही है"; "ta" -> "உதவி தொடங்குகிறது"; "kn" -> "ಸಹಾಯ ಪ್ರಾರಂಭವಾಗುತ್ತಿದೆ"
                "ml" -> "സഹായം ആരംഭിക്കുന്നു"; "te" -> "సహాయం ప్రారంభమవుతోంది"; else -> "Assistance starting"
            }
            "op_starting_no_cpu_keepalive" -> when (languageCode) {
                "hi" -> "सहायता शुरू हो रही है। पावर चालू रखने की सुविधा उपलब्ध नहीं। फ़ोन सोया तो सहायता रुक सकती है। बेंत या गाइड का उपयोग करें।"
                "ta" -> "உதவி தொடங்குகிறது. மின்சாரம் விழித்திருக்க வைக்கும் வசதி கிடைக்கவில்லை. தொலைபேசி உறங்கினால் உதவி நிற்கலாம். கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಸಹಾಯ ಪ್ರಾರಂಭವಾಗುತ್ತಿದೆ. ವಿದ್ಯುತ್ ಎಚ್ಚರವಿರಿಸುವ ಸೌಲಭ್ಯ ಲಭ್ಯವಿಲ್ಲ. ದೂರವಾಣಿ ನಿದ್ರೆಗೆ ಹೋದರೆ ಸಹಾಯ ನಿಲ್ಲಬಹುದು. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "സഹായം ആരംഭിക്കുന്നു. പവർ ഉണർന്നിരിക്കാൻ സഹായം ലഭ്യമല്ല. ഫോൺ ഉറങ്ങിയാൽ സഹായം നിൽക്കാം. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "సహాయం ప్రారంభమవుతోంది. పవర్ మేల్కొని ఉంచే సౌకర్యం లేదు. ఫోన్ నిద్రపోతే సహాయం ఆగవచ్చు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Assistance starting. Power keep-alive unavailable. Assistance may stop if the phone sleeps. Use cane or guide."
            }
            "op_starting_no_screen_keepalive" -> when (languageCode) {
                "hi" -> "सहायता शुरू हो रही है। स्क्रीन चालू रखें, नहीं तो सोने पर सहायता रुक जाएगी। स्वयंसेवक से अन्य ऐप्स के ऊपर प्रदर्शन की अनुमति माँगें।"
                "ta" -> "உதவி தொடங்குகிறது. திரையை இயக்கத்தில் வையுங்கள், இல்லையெனில் உறங்கும்போது உதவி நிற்கும். பிற செயலிகளுக்கு மேல் காட்ட அனுமதி தன்னார்வலரிடம் கேளுங்கள்."
                "kn" -> "ಸಹಾಯ ಪ್ರಾರಂಭವಾಗುತ್ತಿದೆ. ತೆರೆಯನ್ನು ಆನ್ ಇರಿಸಿ, ಇಲ್ಲದಿದ್ದರೆ ನಿದ್ರೆಯಲ್ಲಿ ಸಹಾಯ ನಿಲ್ಲುತ್ತದೆ. ಇತರ ಅನ್ವಯಗಳ ಮೇಲೆ ತೋರಿಸಲು ಸ್ವಯಂಸೇವಕರಿಂದ ಅನುಮತಿ ಕೇಳಿ."
                "ml" -> "സഹായം ആരംഭിക്കുന്നു. സ്ക്രീൻ ഓണായി വയ്ക്കുക, അല്ലെങ്കിൽ ഉറങ്ങുമ്പോൾ സഹായം നിൽക്കും. മറ്റ് ആപ്പുകൾക്ക് മുകളിൽ കാണിക്കാൻ സന്നദ്ധപ്രവർത്തകനോട് അനുവാദം ചോദിക്കുക."
                "te" -> "సహాయం ప్రారంభమవుతోంది. స్క్రీన్ ఆన్‌లో ఉంచండి, లేకపోతే నిద్రపోతే సహాయం ఆగుతుంది. ఇతర యాప్‌లపై చూపడానికి స్వచ్ఛంద సేవకుని అనుమతి అడగండి."
                else -> "Assistance starting. Keep the screen on, or assistance will stop when it sleeps. Ask a volunteer to allow Display over other apps."
            }
            "op_phone_tilted" -> when (languageCode) {
                "hi" -> "फ़ोन झुका है। कैमरा आगे की ओर रखें।"
                "ta" -> "தொலைபேசி சாய்ந்துள்ளது. கேமராவை முன்னே காட்டுங்கள்."
                "kn" -> "ದೂರವಾಣಿ ಓರೆಯಾಗಿದೆ. ಕ್ಯಾಮೆರಾವನ್ನು ಮುಂದಕ್ಕೆ ಇರಿಸಿ."
                "ml" -> "ഫോൺ ചായ്‌ന്നിരിക്കുന്നു. ക്യാമറ മുന്നോട്ട് വയ്ക്കുക."
                "te" -> "ఫోన్ వంగి ఉంది. కెమెరాను ముందుకు ఉంచండి."
                else -> "Phone tilted. Point camera forward."
            }
            "op_thermal_slow" -> when (languageCode) {
                "hi" -> "फ़ोन ठंडा होने के लिए धीमा चल रहा है"
                "ta" -> "தொலைபேசி குளிர்வடைய மெதுவாக இயங்குகிறது"
                "kn" -> "ದೂರವಾಣಿ ತಣ್ಣಗಾಗಲು ನಿಧಾನವಾಗಿ ಚಾಲನೆಯಲ್ಲಿದೆ"
                "ml" -> "ഫോൺ തണുക്കാൻ മന്ദഗതിയിൽ പ്രവർത്തിക്കുന്നു"
                "te" -> "ఫోన్ చల్లబడటానికి నెమ్మదిగా నడుస్తోంది"
                else -> "Phone is running slower to cool down"
            }
            "op_battery_low" -> when (languageCode) {
                "hi" -> "बैटरी कम है। दृष्टि अलर्ट जल्द रुक सकते हैं।"
                "ta" -> "மின்கலம் குறைவு. பார்வை எச்சரிக்கைகள் விரைவில் நிற்கலாம்."
                "kn" -> "ಬ್ಯಾಟರಿ ಕಡಿಮೆ. ದೃಷ್ಟಿ ಎಚ್ಚರಿಕೆಗಳು ಶೀಘ್ರದಲ್ಲೇ ನಿಲ್ಲಬಹುದು."
                "ml" -> "ബാറ്ററി കുറവാണ്. കാഴ്ച മുന്നറിയിപ്പുകൾ ഉടൻ നിൽക്കാം."
                "te" -> "బ్యాటరీ తక్కువ. దృష్టి హెచ్చరికలు త్వరలో ఆగవచ్చు."
                else -> "Battery low. Vision alerts may stop soon."
            }
            "op_battery_critical" -> when (languageCode) {
                "hi" -> "बैटरी गंभीर रूप से कम है। दृष्टि सहायता रुकी। बेंत या गाइड का उपयोग करें।"
                "ta" -> "மின்கலம் மிகவும் குறைவு. பார்வை உதவி நின்றது. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ಬ್ಯಾಟರಿ ತೀವ್ರವಾಗಿ ಕಡಿಮೆ. ದೃಷ್ಟಿ ಸಹಾಯ ನಿಂತಿದೆ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "ബാറ്ററി ഗുരുതരമായി കുറവാണ്. കാഴ്ച സഹായം നിന്നു. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "బ్యాటరీ తీవ్రంగా తక్కువ. దృష్టి సహాయం ఆగింది. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Battery critical. Vision assistance stopped. Use cane or guide."
            }
            "op_power_keepalive_lost" -> when (languageCode) {
                "hi" -> "पावर चालू रखने की सुविधा उपलब्ध नहीं। फ़ोन सोया तो सहायता रुक सकती है। बेंत या गाइड का उपयोग करें।"
                "ta" -> "மின்சாரம் விழித்திருக்க வைக்கும் வசதி கிடைக்கவில்லை. தொலைபேசி உறங்கினால் உதவி நிற்கலாம். கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்."
                "kn" -> "ವಿದ್ಯುತ್ ಎಚ್ಚರವಿರಿಸುವ ಸೌಲಭ್ಯ ಲಭ್ಯವಿಲ್ಲ. ದೂರವಾಣಿ ನಿದ್ರೆಗೆ ಹೋದರೆ ಸಹಾಯ ನಿಲ್ಲಬಹುದು. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ."
                "ml" -> "പവർ ഉണർന്നിരിക്കാൻ സഹായം ലഭ്യമല്ല. ഫോൺ ഉറങ്ങിയാൽ സഹായം നിൽക്കാം. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക."
                "te" -> "పవర్ మేల్కొని ఉంచే సౌకర్యం లేదు. ఫోన్ నిద్రపోతే సహాయం ఆగవచ్చు. కర్ర లేదా గైడ్ ఉపయోగించండి."
                else -> "Power keep-alive unavailable. Assistance may stop if the phone sleeps. Use cane or guide."
            }
            "op_headset_disconnected" -> when (languageCode) {
                "hi" -> "हेडसेट डिस्कनेक्ट हो गया। अलर्ट अब स्पीकर पर बजेंगे।"
                "ta" -> "ஹெட்செட் துண்டிக்கப்பட்டது. எச்சரிக்கைகள் இப்போது ஸ்பீக்கரில் ஒலிக்கும்."
                "kn" -> "ಹೆಡ್‌ಸೆಟ್ ಸಂಪರ್ಕ ಕಡಿತಗೊಂಡಿದೆ. ಎಚ್ಚರಿಕೆಗಳು ಈಗ ಸ್ಪೀಕರ್‌ನಲ್ಲಿ ನುಡಿಯುತ್ತವೆ."
                "ml" -> "ഹെഡ്‌സെറ്റ് വിച്ഛേദിച്ചു. മുന്നറിയിപ്പുകൾ ഇപ്പോൾ സ്പീക്കറിൽ കേൾക്കും."
                "te" -> "హెడ్‌సెట్ డిస్‌కనెక్ట్ అయింది. హెచ్చరికలు ఇప్పుడు స్పీకర్‌లో వినిపిస్తాయి."
                else -> "Headset disconnected. Alerts now play on the speaker."
            }
            "op_env_dark" -> when (languageCode) {
                "hi" -> "आसपास अँधेरा है।"
                "ta" -> "சுற்றுப்புறம் இருட்டாக உள்ளது."
                "kn" -> "ಸುತ್ತಲೂ ಕತ್ತಲಾಗಿದೆ."
                "ml" -> "ചുറ്റുപാട് ഇരുണ്ടതാണ്."
                "te" -> "చుట్టూ చీకటిగా ఉంది."
                else -> "Environment is dark."
            }
            "op_env_bright" -> when (languageCode) {
                "hi" -> "अब ज़्यादा रोशनी है।"
                "ta" -> "இப்போது வெளிச்சம் அதிகம்."
                "kn" -> "ಈಗ ಹೆಚ್ಚು ಬೆಳಕಿದೆ."
                "ml" -> "ഇപ്പോൾ കൂടുതൽ വെളിച്ചമുണ്ട്."
                "te" -> "ఇప్పుడు ఎక్కువ వెలుతురు ఉంది."
                else -> "Brighter now."
            }
            else -> ""
        }
    }

    private enum class SpeechPriority(val rank: Int) { STATUS(0), AWARENESS(1), CONTROL_OR_URGENT(2) }
    private data class PendingSpeech(
        val text: String,
        val onComplete: (() -> Unit)?,
        val priority: SpeechPriority,
        val bypassMute: Boolean
    )
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
    private var pendingStatus: PendingSpeech? = null
    private var pendingAnnounce: PendingAnnounce? = null
    private var pendingAnnounceFuture: ScheduledFuture<*>? = null
    private var pendingMuteExpiryFuture: ScheduledFuture<*>? = null
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
    private val hapticFeedbackEngine = HapticFeedbackEngine(context)
    private val audioManager = context.getSystemService(AudioManager::class.java)
    private val focusListener = AudioManager.OnAudioFocusChangeListener { /* ducking is transient; no state needed */ }
    private val focusRequest: AudioFocusRequest = AlertAudioFocus.buildRequest(focusListener)
    /** Nested speak/flush holds: abandon system focus only when the last utterance ends. */
    private val focusHoldCount = java.util.concurrent.atomic.AtomicInteger(0)
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
                    toSpeak?.let {
                        speak(
                            it.text,
                            flush = true,
                            id = nextStatusId(),
                            onComplete = it.onComplete,
                            bypassMute = it.bypassMute,
                            priority = it.priority
                        )
                    }
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
                    }.onFailure {
                        Log.e("AkshravaVision", "TTS rebuild scheduling failed", it)
                        val toDrop = synchronized(completionLock) {
                            pendingStatus.also { pendingStatus = null }
                        }
                        toDrop?.onComplete?.invoke()
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
        runCatching { api.execute(block) }.onFailure {
            Log.e("AkshravaVision", "speech operation scheduling failed", it)
        }
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
        if (haptic == "none" || haptic.isEmpty()) {
            hapticFeedbackEngine.playBearingCue(bearing)
        } else {
            vibrate(haptic)
        }
        if (now < mutedUntilMs) return
        // S1 cuts a CAUTION utterance mid-word; interruption itself signals urgency. But an
        // urgent phrase's own first 350 ms is protected: a second urgent alert queues behind it
        // instead of flushing, so the head of the first warning is always comprehensible.
        val protectingUrgentHead = urgent && now - lastUrgentSpokenAtMs < URGENT_PROTECT_MS
        if (urgent) lastUrgentSpokenAtMs = now
        speak(
            text,
            flush = urgent && !protectingUrgentHead,
            id = cooldownKey,
            priority = if (urgent) SpeechPriority.CONTROL_OR_URGENT else SpeechPriority.AWARENESS
        )
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

    /** Double-press headset mute. A second double-press resumes early; expiry also speaks. */
    fun toggleMute(durationMs: Long = MUTE_AUTO_EXPIRE_MS) {
        runApi {
            val now = SystemClock.elapsedRealtime()
            val nextUntil = nextMuteUntil(now, mutedUntilMs, durationMs)
            pendingMuteExpiryFuture?.cancel(false)
            pendingMuteExpiryFuture = null
            mutedUntilMs = nextUntil
            if (nextUntil == 0L) {
                speak(
                    muteResumedText(), flush = true, id = nextStatusId(), bypassMute = true,
                    priority = SpeechPriority.CONTROL_OR_URGENT
                )
                return@runApi
            }
            speak(
                mutedText(), flush = true, id = nextStatusId(), bypassMute = true,
                priority = SpeechPriority.CONTROL_OR_URGENT
            )
            pendingMuteExpiryFuture = runCatching {
                api.schedule({
                    if (mutedUntilMs != nextUntil) return@schedule
                    mutedUntilMs = 0L
                    pendingMuteExpiryFuture = null
                    speak(
                        muteResumedText(), flush = true, id = nextStatusId(), bypassMute = true,
                        priority = SpeechPriority.CONTROL_OR_URGENT
                    )
                }, (nextUntil - now).coerceAtLeast(1L), TimeUnit.MILLISECONDS)
            }.getOrElse {
                mutedUntilMs = 0L
                Log.e("AkshravaVision", "mute expiry scheduling failed", it)
                speak(
                    muteResumedText(), flush = true, id = nextStatusId(), bypassMute = true,
                    priority = SpeechPriority.CONTROL_OR_URGENT
                )
                null
            }
        }
    }

    private fun mutedText(): String = when (languageCode) {
        "hi" -> "दो मिनट के लिए म्यूट"
        "ta" -> "இரண்டு நிமிடங்களுக்கு ஒலி நிறுத்தப்பட்டது"
        "kn" -> "ಎರಡು ನಿಮಿಷಗಳ ಕಾಲ ಮ್ಯೂಟ್ ಮಾಡಲಾಗಿದೆ"
        "ml" -> "രണ്ട് മിനിറ്റേക്ക് മ്യൂട്ട് ചെയ്തു"
        "te" -> "రెండు నిమిషాల పాటు మ్యూట్ చేయబడింది"
        else -> "Muted for two minutes"
    }

    private fun muteResumedText(): String = when (languageCode) {
        "hi" -> "आवाज़ फिर चालू"
        "ta" -> "பேச்சு மீண்டும் தொடங்கியது"
        "kn" -> "ಮಾತು ಮತ್ತೆ ಆರಂಭವಾಗಿದೆ"
        "ml" -> "സംസാരം വീണ്ടും തുടങ്ങി"
        "te" -> "మాటలు మళ్లీ ప్రారంభమయ్యాయి"
        else -> "Speech resumed"
    }

    /** Single-press headset repeat: replays the last spoken alert if it is still recent. */
    fun repeatLast() {
        runApi {
            val text = lastAlertText
            val now = SystemClock.elapsedRealtime()
            if (text == null || now - lastAlertAtMs > REPEATABLE_WINDOW_MS) {
                speak(
                    if (isHindi) "कोई हाल का अलर्ट नहीं" else "No recent alert",
                    flush = true, id = nextStatusId(),
                    priority = SpeechPriority.CONTROL_OR_URGENT
                )
                return@runApi
            }
            speak(
                text, flush = true, id = nextStatusId(),
                priority = SpeechPriority.CONTROL_OR_URGENT
            )
        }
    }

    /**
     * True while a hazard alert spoken within [windowMs] is still the freshest thing in the
     * user's only audio channel.
     *
     * [status] flushes TTS, which is right for every prompt that means "the camera cannot see" —
     * assistance is degraded and the user needs to know now. It is the wrong trade for context
     * that makes no claim about what is ahead (F-71 ambient light), so that tier checks this and
     * drops itself rather than cutting off "Vehicle nearby".
     */
    fun hazardSpokenWithin(nowMs: Long, windowMs: Long = MIN_UTTERANCE_GAP_MS): Boolean =
        lastAlertAtMs != 0L && nowMs - lastAlertAtMs < windowMs

    fun operationalText(key: String): String = Companion.operationalText(key, languageCode)

    fun status(text: String, haptic: Boolean = false, onComplete: (() -> Unit)? = null) {
        runApi {
            if (haptic) vibrate("single")
            speak(text, flush = true, id = nextStatusId(), onComplete = onComplete)
        }
    }

    fun statusKey(key: String, haptic: Boolean = false, onComplete: (() -> Unit)? = null) {
        val text = operationalText(key)
        if (text.isEmpty()) {
            Log.e("AkshravaVision", "missing_operational_speech key=$key")
            return
        }
        status(text, haptic, onComplete)
    }

    /** Speak then run [onDone] (camera-failure teardown, etc.). */
    fun speakThen(text: String, utteranceId: String = "speak_then", onDone: () -> Unit) {
        status(text, onComplete = onDone)
    }

    fun speakThenKey(key: String, onDone: () -> Unit) {
        val text = operationalText(key)
        if (text.isEmpty()) {
            Log.e("AkshravaVision", "missing_operational_speech key=$key")
            onDone()
            return
        }
        speakThen(text, onDone = onDone)
    }

    /** User-pulled look summary: flush and bypass object cooldown / busy collapse. */
    fun speakComposed(text: String, urgent: Boolean = true) {
        runApi {
            val now = SystemClock.elapsedRealtime()
            markUtterance(now)
            speak(
                text,
                flush = urgent,
                id = "look-${++statusSequence}",
                priority = if (urgent) SpeechPriority.CONTROL_OR_URGENT else SpeechPriority.AWARENESS
            )
        }
    }

    /** Every control action must be confirmed by voice (§6.4): an explicit look that never got
     * an answer -- send failure, or no priority result within its timeout -- must not resolve
     * into silence just because nothing came back. */
    fun announceLookFailed() {
        speakComposed(operationalText("op_look_unavailable"))
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
        val text = template("busy_road", "ahead")
        vibrate("single")
        speak(text, flush = true, id = "summary", priority = SpeechPriority.AWARENESS)
    }

    private fun pruneRecent(now: Long) {
        while (recentUtterances.isNotEmpty()) {
            val first = recentUtterances.peekFirst() ?: break
            if (now - first > BUSY_WINDOW_MS) {
                recentUtterances.pollFirst()
            } else {
                break
            }
        }
    }

    private fun markUtterance(now: Long) {
        lastUtteranceMs = now
        recentUtterances.addLast(now)
    }

    private fun speak(
        text: String,
        flush: Boolean,
        id: String,
        onComplete: (() -> Unit)? = null,
        bypassMute: Boolean = false,
        priority: SpeechPriority = SpeechPriority.STATUS
    ) {
        if (speechSuppressedByMute(SystemClock.elapsedRealtime(), mutedUntilMs, bypassMute)) {
            // Deliberate mute covers every TTS entry point, not only hazard templates. Completion
            // callbacks still run so a muted teardown/status flow can never hang waiting for speech.
            onComplete?.let { mainHandler.post(it) }
            return
        }
        if (!ready) {
            // Engine is (re)connecting. Keep the NEWEST utterance for delivery after init —
            // onInit speaks pendingStatus on success — instead of dropping it into silence.
            // For a blind user a late warning still beats no warning.
            retainPendingSpeech(PendingSpeech(text, onComplete, priority, bypassMute))
            return
        }
        if (onComplete != null) synchronized(completionLock) { completionCallbacks[id] = onComplete }
        // Duck TalkBack / media briefly so the alert is audible (F-09). Failure to gain focus
        // must not suppress speech — a quiet alert still beats silence mid-walk.
        // Refcount holds across flush/queue so a prior utterance's onStop cannot abandon focus
        // while a newer one is still speaking.
        acquireAudioFocus()
        val result = tts?.speak(
            text, if (flush) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, null, id
        ) ?: TextToSpeech.ERROR
        if (result == TextToSpeech.SUCCESS) {
            // The engine accepted the utterance: the binding is alive, so recovery quota refills.
            engineRebuildStreak = 0
            return
        }
        releaseAudioFocus()
        // speak() returning ERROR after successful init means the engine connection is dead
        // (verified live: OEM force-stop of com.google.android.tts -> "speak failed: not bound
        // to TTS engine" on every call, forever). The framework never rebinds; we must.
        synchronized(completionLock) { completionCallbacks.remove(id) }
        AgentDebugLog.log(
            "H7", "AlertManager.speak:engineDead", "tts_speak_failed",
            mapOf("id" to id, "streak" to engineRebuildStreak)
        )
        rebuildEngine(text, onComplete, priority, bypassMute)
    }

    /** Keep one reconnecting-engine utterance without letting status replace awareness. */
    private fun retainPendingSpeech(candidate: PendingSpeech) {
        val dropped = synchronized(completionLock) {
            val existing = pendingStatus
            if (existing == null || candidate.priority.rank >= existing.priority.rank) {
                pendingStatus = candidate
                existing
            } else {
                candidate
            }
        }
        dropped?.onComplete?.invoke()
    }

    /**
     * Tear down the dead TextToSpeech client and bind a fresh one, re-queueing [text] so the
     * failed utterance is spoken as soon as the new engine initialises. Bounded by
     * [engineRebuildAllowed]; when the quota is exhausted the callback still runs (teardown
     * paths depend on it) and haptics remain the surviving alert channel — deliverAnnounce
     * vibrates before speaking, so S1 buzzes continue even with speech gone.
     */
    private fun rebuildEngine(
        text: String?,
        onComplete: (() -> Unit)?,
        priority: SpeechPriority = SpeechPriority.STATUS,
        bypassMute: Boolean = false
    ) {
        if (closed) {
            onComplete?.invoke()
            return
        }
        ready = false
        if (text != null) {
            retainPendingSpeech(PendingSpeech(text, onComplete, priority, bypassMute))
        }
        val now = SystemClock.elapsedRealtime()
        if (engineRebuildStreak >= ENGINE_REBUILD_MAX_STREAK) {
            Log.e("AkshravaVision", "TTS rebuild limit reached")
            val toDrop = synchronized(completionLock) { pendingStatus.also { pendingStatus = null } }
            toDrop?.onComplete?.invoke()
            return
        }
        val elapsed = if (lastEngineRebuildMs == 0L) Long.MAX_VALUE else now - lastEngineRebuildMs
        if (elapsed < ENGINE_REBUILD_MIN_INTERVAL_MS) {
            val retryAfterMs = ENGINE_REBUILD_MIN_INTERVAL_MS - elapsed
            runCatching {
                api.schedule(
                    { rebuildEngine(null, null) }, retryAfterMs, TimeUnit.MILLISECONDS
                )
            }.onFailure {
                Log.e("AkshravaVision", "TTS rebuild retry scheduling failed", it)
                val toDrop = synchronized(completionLock) {
                    pendingStatus.also { pendingStatus = null }
                }
                toDrop?.onComplete?.invoke()
            }
            return
        }
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
            // Assign in one expression so the field is set the instant the constructor returns,
            // before this main-thread runnable yields. Some OEM ROMs invoke onInit synchronously
            // from the TextToSpeech constructor; that path only queues work onto `api`, and by
            // the time that work runs `tts` must already be non-null — otherwise speak() returns
            // ERROR and rebuild-streak exhaustion permanently kills speech.
            //
            // Do NOT split into `val engine = TextToSpeech(...); tts = engine`: that widens the
            // same race and can leak the engine if `closed` flips between construct and assign.
            tts = TextToSpeech(context, this)
            if (closed) {
                runCatching { tts?.shutdown() }
                tts = null
            }
        }
    }

    private fun nextStatusId(): String = "status-${++statusSequence}"

    private fun complete(utteranceId: String) {
        releaseAudioFocus()
        val callback = synchronized(completionLock) { completionCallbacks.remove(utteranceId) }
        callback?.let { mainHandler.post(it) }
    }

    /**
     * Take one hold for the utterance about to be queued.
     *
     * The hold is taken whether or not the system grants focus, because the hold is what pairs
     * with the release in [complete] — every utterance handed to the engine gets exactly one
     * onDone/onStop/onError. Counting only *granted* requests meant a denial (or a speak issued
     * during teardown) later decremented a hold belonging to a different, still-speaking
     * utterance, abandoning the duck in the middle of an alert.
     */
    private fun acquireAudioFocus() {
        val first = focusHoldCount.getAndIncrement() == 0
        if (first && AlertAudioFocus.shouldRequest(ready = ready, closed = closed)) {
            // A denial is not fatal: a quiet alert still beats silence, so speech proceeds either way.
            AlertAudioFocus.request(audioManager, focusRequest)
        }
    }

    private fun releaseAudioFocus() {
        // Clamped at zero so a stray release can never drive the count negative and strand a
        // later duck (the count would have to climb back through the deficit before requesting).
        val remaining = focusHoldCount.updateAndGet { if (it > 0) it - 1 else 0 }
        if (remaining == 0) AlertAudioFocus.abandon(audioManager, focusRequest)
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
        hapticFeedbackEngine.playPattern(pattern)
    }

    fun shutdown() {
        closed = true
        cancelDeferredCaution()
        pendingMuteExpiryFuture?.cancel(false)
        pendingMuteExpiryFuture = null
        mutedUntilMs = 0L
        focusHoldCount.set(0)
        AlertAudioFocus.abandon(audioManager, focusRequest)
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
