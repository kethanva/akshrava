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

    public static func engineRebuildAllowed(nowMs: Int64, lastRebuildMs: Int64, streak: Int) -> Bool {
        return streak < engineRebuildMaxStreak &&
            (lastRebuildMs == 0 || nowMs - lastRebuildMs >= engineRebuildMinIntervalMs)
    }

    private let synthesizer = AVSpeechSynthesizer()
    private var isMuted = false
    private var lastSpokenMessage: String = ""
    private var lastSpokenTime: Date = Date.distantPast
    private var lastHazardSpokenAt = Date.distantPast

    private let templates: [String: [String: String]] = [
        "en": [
            "vehicle_nearby": "Vehicle nearby",
            "person_ahead": "Person ahead",
            "obstacle_ahead": "Obstacle ahead",
            "look_clear": "No alert in recent view. Continue using cane or guide",
            "connection_open": "Vision assistance connected",
            "connection_dropped": "Connection lost. Use cane or guide",
            "connection_restored": "Connection restored",
            "provisioning_required": "Provisioning required. Add device token before starting.",
            "camera_dark": "Camera is dark. Uncover the rear lens.",
            "camera_glare": "Camera blinded by light. Turn slightly.",
            "camera_blur": "Camera is blurry. Wipe the lens. Use cane or guide.",
            "camera_stall": "Camera stalled. Recovering.",
        ],
        "hi": [
            "vehicle_nearby": "वाहन पास है",
            "person_ahead": "व्यक्ति आगे है",
            "obstacle_ahead": "बाधा आगे है",
            "look_clear": "हाल के दृश्य में कोई अलर्ट नहीं. बेंत या गाइड का उपयोग जारी रखें",
            "connection_open": "दृष्टि सहायता कनेक्ट हो गई है",
            "connection_dropped": "कनेक्शन टूट गया. बेंत या गाइड का प्रयोग करें",
            "connection_restored": "कनेक्शन बहाल हो गया",
            "provisioning_required": "प्रावधान आवश्यक है. शुरू करने से पहले डिवाइस टोकन जोड़ें.",
            "camera_dark": "कैमरा अँधेरा है. पीछे का लेंस खोलें.",
            "camera_glare": "कैमरे पर तेज़ रोशनी है. थोड़ा मुड़ें.",
            "camera_blur": "कैमरा धुँधला है. लेंस पोंछें. बेंत या गाइड का उपयोग करें.",
            "camera_stall": "कैमरा रुक गया. पुनर्प्राप्ति हो रही है.",
        ],
    ]

    private override init() {
        super.init()
        synthesizer.delegate = self
        setupAudioSession()
    }

    private func setupAudioSession() {
        #if os(iOS)
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .spokenAudio, options: [.duckOthers, .mixWithOthers])
            try session.setActive(true)
        } catch {
            AgentDebugLog.log(message: "AudioSession setup failed: \(error)")
        }
        #endif
    }

    public func speak(messageKey: String, language: String = "en", force: Bool = false) {
        let text = templates[language]?[messageKey] ?? templates["en"]?[messageKey] ?? messageKey
        speakStatus(text, language: language, force: force)
    }

    /// Speak server `spoken_preview` or any already-rendered awareness string.
    /// The `language` parameter selects the BCP-47 voice (e.g. "hi" → "hi-IN"); defaults to en-IN.
    public func speakStatus(_ text: String, language: String = "en", force: Bool = false) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        guard !isMuted else { return }

        if !force && trimmed == lastSpokenMessage && Date().timeIntervalSince(lastSpokenTime) < 1.5 {
            return
        }

        lastSpokenMessage = trimmed
        lastSpokenTime = Date()

        let utterance = AVSpeechUtterance(string: trimmed)
        // Map wire-code prefix to BCP-47 locale; fall back to en-IN for unknown tags.
        let bcp47 = Self.bcpVoice(for: language)
        utterance.voice = AVSpeechSynthesisVoice(language: bcp47) ?? AVSpeechSynthesisVoice(language: "en-IN")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate

        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
        synthesizer.speak(utterance)
    }

    public func speakHazardPreview(_ text: String, language: String = "en") {
        speakStatus(text, language: language, force: true)
        lastHazardSpokenAt = Date()
    }

    /// True when a hazard was spoken recently — ambient edges must yield (Android hazardSpokenWithin).
    public func hazardSpokenWithin(ms: Int64) -> Bool {
        Date().timeIntervalSince(lastHazardSpokenAt) * 1000.0 < Double(ms)
    }

    public func toggleMute() {
        isMuted.toggle()
        if isMuted {
            synthesizer.stopSpeaking(at: .immediate)
        }
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
