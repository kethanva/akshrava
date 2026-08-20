//
//  AlertManager.swift
//  Akshrava iOS
//
//  TTS speech output — mirrors Android AlertManager.kt.
//  Safety boundary: Awareness only. Strictly avoids directional route guarantees.
//

import Foundation
import AVFoundation

public class AlertManager: NSObject, AVSpeechSynthesizerDelegate {
    public static let shared = AlertManager()

    public static let engineRebuildMaxStreak: Int = 4
    public static let engineRebuildMinIntervalMs: Int64 = 4_000
    /// Mirrors Android's headset-double-press mute, which auto-expires rather than latching
    /// permanently: an accidental trigger on an app with no visible UI must not silence a blind
    /// user's only feedback channel indefinitely.
    public static let muteAutoExpireSeconds: Double = 120

    public static func engineRebuildAllowed(nowMs: Int64, lastRebuildMs: Int64, streak: Int) -> Bool {
        return streak < engineRebuildMaxStreak &&
            (lastRebuildMs == 0 || nowMs - lastRebuildMs >= engineRebuildMinIntervalMs)
    }

    /// Relative importance of a spoken utterance. A lower-priority status message (ambient light,
    /// "Looking.", connection state) must never truncate a higher-priority hazard announcement
    /// mid-word -- that previously happened because every `speakStatus(force: true)` call
    /// unconditionally interrupted whatever was speaking, with no notion of what mattered more.
    private enum Priority: Int, Comparable {
        case status = 0
        case hazard = 1
        static func < (lhs: Priority, rhs: Priority) -> Bool { lhs.rawValue < rhs.rawValue }
    }

    private let synthesizer = AVSpeechSynthesizer()
    // Every field below except `hazardLock`-guarded `lastHazardSpokenAt` is confined to the main
    // thread: `speak`/`speakStatus`/`speakHazardPreview` all funnel through `enqueueSpeak`, which
    // dispatches onto main before touching any of them, and AVSpeechSynthesizerDelegate callbacks
    // are documented to arrive on main. `lastHazardSpokenAt` is the one field genuinely read from
    // another thread (AssistSessionManager's video-sample-buffer queue, via
    // `hazardSpokenWithin`), so it alone gets its own lock rather than being folded into the
    // main-thread confinement the rest of this class relies on.
    private var isMuted = false
    private var lastSpokenMessage: String = ""
    private var lastSpokenTime: Date = Date.distantPast
    private var currentPriority: Priority = .status
    /// The utterance `currentPriority` describes. Held so a late delegate callback for an
    /// already-superseded utterance cannot clear the current one's priority.
    private weak var speakingUtterance: AVSpeechUtterance?
    private var muteExpiryWorkItem: DispatchWorkItem?

    private let hazardLock = NSLock()
    private var _lastHazardSpokenAt = Date.distantPast

    private let templates: [String: [String: String]] = [
        "en": [
            "vehicle_nearby": "Vehicle nearby",
            "person_ahead": "Person ahead",
            "obstacle_ahead": "Obstacle ahead",
            "busy_road": "Busy road, careful",
            "connection_open": "Vision assistance connected",
            "connection_dropped": "Connection lost. Use cane or guide",
            "connection_restored": "Connection restored",
            "provisioning_required": "Provisioning required. Add device token before starting.",
            "camera_dark": "Camera is dark. Uncover the rear lens.",
            "camera_glare": "Camera blinded by light. Turn slightly.",
            "camera_blur": "Camera is blurry. Wipe the lens. Use cane or guide.",
            "camera_stall": "Camera stalled. Recovering.",
            "look_unavailable": "Look unavailable. Use cane or guide.",
            "server_shedding": "Server busy. Use cane or guide.",
            "watchdog_stalled": "Open Akshrava and press Start assistance again. Keep using your cane.",
        ],
        "hi": [
            "vehicle_nearby": "वाहन पास है",
            "person_ahead": "व्यक्ति आगे है",
            "obstacle_ahead": "बाधा आगे है",
            "busy_road": "व्यस्त सड़क, सावधान",
            "connection_open": "दृष्टि सहायता जुड़ गई",
            "connection_dropped": "दृष्टि सहायता उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।",
            "connection_restored": "कनेक्शन बहाल हो गया",
            "provisioning_required": "प्रावधान आवश्यक है. शुरू करने से पहले डिवाइस टोकन जोड़ें.",
            "camera_dark": "कैमरा अँधेरा है. पीछे का लेंस खोलें.",
            "camera_glare": "कैमरे पर तेज़ रोशनी है. थोड़ा मुड़ें.",
            "camera_blur": "कैमरा धुँधला है. लेंस पोंछें. बेंत या गाइड का उपयोग करें.",
            "camera_stall": "कैमरा रुक गया. पुनर्प्राप्ति हो रही है.",
            "look_unavailable": "लुक उपलब्ध नहीं। बेंत या गाइड का उपयोग करें।",
            "server_shedding": "सर्वर व्यस्त है। बेंत या गाइड का उपयोग करें।",
            "watchdog_stalled": "अक्षरव खोलें और सहायता शुरू करें दबाएँ। छड़ी का उपयोग जारी रखें।",
        ],
        "ta": [
            "vehicle_nearby": "அருகில் வாகனம்",
            "person_ahead": "முன்னே நபர் உள்ளார்",
            "obstacle_ahead": "முன்னே தடையுள்ளது",
            "busy_road": "பரபரப்பான சாலை, கவனம்",
            "connection_open": "பார்வை உதவி இணைந்தது",
            "connection_dropped": "பார்வை உதவி கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்.",
            "connection_restored": "இணைப்பு மீண்டும் வந்தது",
            "provisioning_required": "சாதன அங்கீகாரம் தேவை. தொடங்குவதற்கு முன் சாதன அடையாளத்தைச் சேர்க்கவும்.",
            "camera_dark": "கேமரா இருட்டாக உள்ளது. பின்புற வில்லையைத் திறக்கவும்.",
            "camera_glare": "கேமரா ஒளியால் மறைந்துள்ளது. சற்று திரும்பவும்.",
            "camera_blur": "கேமரா மங்கலாக உள்ளது. வில்லையைத் துடைக்கவும். கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்.",
            "camera_stall": "கேமரா நின்றது. மீட்டெடுக்கிறது.",
            "look_unavailable": "பார்வை கிடைக்கவில்லை. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்.",
            "server_shedding": "சேவையகம் பணிமிகுதியில் உள்ளது. கோல் அல்லது வழிகாட்டியைப் பயன்படுத்தவும்.",
        ],
        "kn": [
            "vehicle_nearby": "ಹತ್ತಿರ ವಾಹನ",
            "person_ahead": "ಮುಂದೆ ವ್ಯಕ್ತಿ ಇದ್ದಾರೆ",
            "obstacle_ahead": "ಮುಂದೆ ಅಡಚಣೆ ಇದೆ",
            "busy_road": "ಗಿಜಿಗುಡಿದ ರಸ್ತೆ, ಎಚ್ಚರಿಕೆ",
            "connection_open": "ದೃಷ್ಟಿ ಸಹಾಯ ಸಂಪರ್ಕಗೊಂಡಿದೆ",
            "connection_dropped": "ದೃಷ್ಟಿ ಸಹಾಯ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ.",
            "connection_restored": "ಸಂಪರ್ಕ ಮರುಸ್ಥಾಪಿತವಾಗಿದೆ",
            "provisioning_required": "ಸಾಧನದ ಸಿದ್ಧತೆ ಅಗತ್ಯವಿದೆ. ಪ್ರಾರಂಭಿಸುವ ಮೊದಲು ಸಾಧನ ಚೀಟಿ ಸೇರಿಸಿ.",
            "camera_dark": "ಕ್ಯಾಮೆರಾ ಕತ್ತಲಾಗಿದೆ. ಹಿಂದಿನ ಮಸೂರವನ್ನು ತೆರೆಯಿರಿ.",
            "camera_glare": "ಕ್ಯಾಮೆರಾ ಬೆಳಕಿನಿಂದ ಕುರುಡಾಗಿದೆ. ಸ್ವಲ್ಪ ತಿರುಗಿ.",
            "camera_blur": "ಕ್ಯಾಮೆರಾ ಮಬ್ಬಾಗಿದೆ. ಮಸೂರವನ್ನು ಒರೆಸಿ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ.",
            "camera_stall": "ಕ್ಯಾಮೆರಾ ನಿಂತಿದೆ. ಮರುಪಡೆಯಲಾಗುತ್ತಿದೆ.",
            "look_unavailable": "ನೋಟ ಲಭ್ಯವಿಲ್ಲ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ.",
            "server_shedding": "ಪೂರೈಕೆಗಣಕ ಕಾರ್ಯನಿರತ. ಕೋಲು ಅಥವಾ ಮಾರ್ಗದರ್ಶಕ ಬಳಸಿ.",
        ],
        "ml": [
            "vehicle_nearby": "അടുത്ത് വാഹനം",
            "person_ahead": "മുന്നിൽ വ്യക്തിയുണ്ട്",
            "obstacle_ahead": "മുന്നിൽ തടസ്സമുണ്ട്",
            "busy_road": "തിരക്കേറിയ റോഡ്, ശ്രദ്ധിക്കുക",
            "connection_open": "കാഴ്ച സഹായം ബന്ധിപ്പിച്ചു",
            "connection_dropped": "കാഴ്ച സഹായം ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക.",
            "connection_restored": "ബന്ധം വീണ്ടും ലഭിച്ചു",
            "provisioning_required": "ഉപകരണ ക്രമീകരണം ആവശ്യമാണ്. ആരംഭിക്കുന്നതിന് മുമ്പ് ഉപകരണ ടോക്കൺ ചേർക്കുക.",
            "camera_dark": "ക്യാമറ ഇരുണ്ടതാണ്. പിന്നിലെ ലെൻസ് തുറന്ന് വയ്ക്കുക.",
            "camera_glare": "ക്യാമറ വെളിച്ചത്താൽ തിളങ്ങുന്നു. അൽപ്പം തിരിയുക.",
            "camera_blur": "ക്യാമറ മങ്ങിയിരിക്കുന്നു. ലെൻസ് തുടയ്ക്കുക. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക.",
            "camera_stall": "ക്യാമറ നിന്നു. വീണ്ടെടുക്കുന്നു.",
            "look_unavailable": "നോട്ട് ലഭ്യമല്ല. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക.",
            "server_shedding": "സെർവർ തിരക്കിലാണ്. വടി അല്ലെങ്കിൽ ഗൈഡ് ഉപയോഗിക്കുക.",
        ],
        "te": [
            "vehicle_nearby": "సమీపంలో వాహనం",
            "person_ahead": "ముందు వ్యక్తి ఉన్నారు",
            "obstacle_ahead": "ముందు అడ్డంకి ఉంది",
            "busy_road": "రద్దీగా ఉన్న రహదారి, జాగ్రత్త",
            "connection_open": "దృష్టి సహాయం అనుసంధానమైంది",
            "connection_dropped": "దృష్టి సహాయం అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి.",
            "connection_restored": "కనెక్షన్ తిరిగి వచ్చింది",
            "provisioning_required": "పరికర సన్నాహం అవసరం. ప్రారంభించే ముందు పరికర టోకెన్ జోడించండి.",
            "camera_dark": "కెమెరా చీకటిగా ఉంది. వెనుక లెన్స్‌ను తెరవండి.",
            "camera_glare": "కెమెరా వెలుతురుతో కప్పబడింది. కొద్దిగా తిరగండి.",
            "camera_blur": "కెమెరా మసకగా ఉంది. లెన్స్ తుడవండి. కర్ర లేదా గైడ్ ఉపయోగించండి.",
            "camera_stall": "కెమెరా ఆగింది. పునరుద్ధరిస్తోంది.",
            "look_unavailable": "చూపు అందుబాటులో లేదు. కర్ర లేదా గైడ్ ఉపయోగించండి.",
            "server_shedding": "సర్వర్ బిజీగా ఉంది. కర్ర లేదా గైడ్ ఉపయోగించండి.",
        ],
    ]

    /// Every spoken string this class can produce from its built-in templates, across all
    /// languages. Exists so tests can audit the ACTUAL spoken text (the safety-boundary audit in
    /// particular) instead of asserting against strings copied into a test file, which drift
    /// silently the moment a template changes and the copy does not.
    public func allTemplateStrings() -> [String] {
        templates.values.flatMap { $0.values }
    }

    private override init() {
        super.init()
        synthesizer.delegate = self
        setupAudioSession()
        #if os(iOS)
        NotificationCenter.default.addObserver(
            self, selector: #selector(handleRouteChange(_:)),
            name: AVAudioSession.routeChangeNotification, object: nil
        )
        #endif
    }

    private func setupAudioSession() {
        #if os(iOS)
        do {
            // .playback, not .playAndRecord: this app never records audio (the only reason a
            // microphone usage description existed at all), and .playAndRecord defaults output
            // to the earpiece receiver when no headset is attached -- inaudible on a phone worn
            // on a lanyard or in a pocket, which is the primary carry mode this app is built for.
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers, .mixWithOthers])
            try session.setActive(true)
        } catch {
            AgentDebugLog.error(event: "audio_session_setup_failed", detail: String(describing: error))
        }
        #endif
    }

    #if os(iOS)
    @objc private func handleRouteChange(_ notification: Notification) {
        guard let reasonValue = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
              let reason = AVAudioSession.RouteChangeReason(rawValue: reasonValue) else { return }
        AgentDebugLog.log(message: "audio_route_change reason=\(reason.rawValue)")
        guard reason == .oldDeviceUnavailable else { return }
        // A headset being unplugged (or any output route disappearing) is never a reason to go
        // quiet. CLAUDE.md is explicit: a device event announces and keeps speaking, because to a
        // user who cannot see the screen, silence is indistinguishable from a dead app.
        // Reactivate defensively first -- some route changes deactivate the session, and a
        // silently-deactivated session drops every subsequent utterance with nothing in any log
        // to explain it -- then say out loud that audio moved, so the change is never silent.
        do {
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            AgentDebugLog.error(event: "audio_session_reactivate_failed", detail: String(describing: error))
        }
        enqueueSpeak(
            "Audio output changed. Assistance continues.",
            language: "en",
            force: true,
            priority: .status
        )
    }
    #endif

    /// Looks up `messageKey` in the requested language, falling back to the English template
    /// (voice and text always agree) and never falling back to speaking the raw key itself: a
    /// missing template previously fell through to speaking machine-readable strings like
    /// "vehicle_nearby" verbatim, in whatever voice `language` selected -- unintelligible in any
    /// language, and especially so when a Tamil/Kannada/Malayalam/Telugu voice was asked to read
    /// English words it has no phonemes for.
    func resolvedTemplate(messageKey: String, language: String) -> (text: String, language: String)? {
        let languageCode = SupportedLanguages.wireCode(for: language)
        if let localized = templates[languageCode]?[messageKey] {
            return (localized, languageCode)
        }
        if let english = templates["en"]?[messageKey] {
            return (english, "en")
        }
        return nil
    }

    public func speak(messageKey: String, language: String = "en", force: Bool = false) {
        if let resolved = resolvedTemplate(messageKey: messageKey, language: language) {
            enqueueSpeak(
                resolved.text,
                language: resolved.language,
                force: force,
                priority: .status
            )
        } else {
            AgentDebugLog.error(
                event: "missing_speech_template",
                detail: "key=\(messageKey) language=\(language)"
            )
        }
    }

    /// Speak server `spoken_preview` or any already-rendered awareness string.
    /// The `language` parameter selects the BCP-47 voice (e.g. "hi" → "hi-IN"); defaults to en-IN.
    public func speakStatus(_ text: String, language: String = "en", force: Bool = false) {
        enqueueSpeak(text, language: language, force: force, priority: .status)
    }

    public func speakHazardPreview(_ text: String, language: String = "en") {
        enqueueSpeak(text, language: language, force: true, priority: .hazard)
        hazardLock.lock()
        _lastHazardSpokenAt = Date()
        hazardLock.unlock()
    }

    /// Resolve a server message key before it reaches the synthesizer. Keys are protocol tokens,
    /// not user-facing speech; unknown keys are logged and suppressed by `speak`.
    public func speakHazard(messageKey: String, language: String = "en") {
        if let resolved = resolvedTemplate(messageKey: messageKey, language: language) {
            enqueueSpeak(
                resolved.text,
                language: resolved.language,
                force: true,
                priority: .hazard
            )
        } else {
            AgentDebugLog.error(
                event: "missing_hazard_template",
                detail: "key=\(messageKey) language=\(language)"
            )
            return
        }
        hazardLock.lock()
        _lastHazardSpokenAt = Date()
        hazardLock.unlock()
    }

    /// True when a hazard was spoken recently — ambient edges must yield (Android hazardSpokenWithin).
    public func hazardSpokenWithin(ms: Int64) -> Bool {
        hazardLock.lock()
        let last = _lastHazardSpokenAt
        hazardLock.unlock()
        return Date().timeIntervalSince(last) * 1000.0 < Double(ms)
    }

    private func enqueueSpeak(_ text: String, language: String, force: Bool, priority: Priority) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        DispatchQueue.main.async { [weak self] in
            self?.speakOnMain(trimmed, language: language, force: force, priority: priority)
        }
    }

    private func speakOnMain(_ trimmed: String, language: String, force: Bool, priority: Priority) {
        guard !isMuted else { return }

        if !force && trimmed == lastSpokenMessage && Date().timeIntervalSince(lastSpokenTime) < 1.5 {
            return
        }

        if synthesizer.isSpeaking {
            // A lower- or equal-priority interruption may proceed only when explicitly forced;
            // critically, nothing may interrupt a HIGHER-priority utterance already in flight, no
            // matter how it is called -- this is what stops an ambient-light status or a "Looking."
            // acknowledgement from cutting off an urgent hazard announcement mid-word.
            guard force, priority >= currentPriority else { return }
            synthesizer.stopSpeaking(at: .word)
        }

        lastSpokenMessage = trimmed
        lastSpokenTime = Date()
        currentPriority = priority

        let utterance = AVSpeechUtterance(string: trimmed)
        // Map wire-code prefix to BCP-47 locale; fall back to en-IN for unknown tags.
        let bcp47 = Self.bcpVoice(for: language)
        utterance.voice = AVSpeechSynthesisVoice(language: bcp47) ?? AVSpeechSynthesisVoice(language: "en-IN")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        // Identity-keyed, so a delegate callback for a PREVIOUS utterance cannot clear the
        // priority of the one now speaking. stopSpeaking() above delivers didCancel
        // asynchronously, which would otherwise land after this line and reset currentPriority to
        // .status -- silently downgrading a just-started hazard and letting the next status
        // message cut it off, the exact bug the priority gate exists to prevent.
        speakingUtterance = utterance
        synthesizer.speak(utterance)
    }

    private func clearPriority(for utterance: AVSpeechUtterance) {
        guard speakingUtterance === utterance else { return }
        speakingUtterance = nil
        currentPriority = .status
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        clearPriority(for: utterance)
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        clearPriority(for: utterance)
    }

    public func toggleMute() {
        DispatchQueue.main.async { [weak self] in
            self?.toggleMuteOnMain()
        }
    }

    private func toggleMuteOnMain() {
        muteExpiryWorkItem?.cancel()
        muteExpiryWorkItem = nil
        if isMuted {
            isMuted = false
            speakOnMain("Speech resumed.", language: "en", force: true, priority: .status)
            return
        }
        synthesizer.stopSpeaking(at: .immediate)
        currentPriority = .status
        // Confirm the deliberate control before engaging the gate. Once AVSpeechSynthesizer has
        // accepted this utterance, the local mute flag does not cancel it; subsequent speech is
        // suppressed until the expiry or another double-press.
        speakOnMain("Speech muted for two minutes.", language: "en", force: true, priority: .status)
        isMuted = true
        // An accidental headset double-press must not silence the app's only feedback channel
        // forever on a screen the user cannot see to notice and undo. Auto-expire, matching the
        // Android mute policy.
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, self.isMuted else { return }
            self.isMuted = false
            self.speakOnMain("Speech resumed.", language: "en", force: true, priority: .status)
        }
        muteExpiryWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + Self.muteAutoExpireSeconds, execute: work)
    }

    /// Maps a wire-code language prefix ("en", "hi", "ta" …) to a BCP-47 voice identifier.
    /// Falls back to "en-IN" for any unknown prefix.
    public static func bcpVoice(for language: String) -> String {
        let tag = language.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let mapping: [String: String] = [
            "en": "en-IN", "hi": "hi-IN", "ta": "ta-IN",
            "kn": "kn-IN", "ml": "ml-IN", "te": "te-IN",
        ]
        if let match = mapping.keys.first(where: { tag.hasPrefix($0) }) {
            return mapping[match] ?? "en-IN"
        }
        return "en-IN"
    }
}
