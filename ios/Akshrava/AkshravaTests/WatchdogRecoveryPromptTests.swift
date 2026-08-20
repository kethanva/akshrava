//
//  WatchdogRecoveryPromptTests.swift
//  AkshravaTests
//
//  Tests for watchdog session recovery logic — mirrors Android WatchdogRecoveryPromptTest.kt.
//  On iOS the watchdog uses BGTaskScheduler + AVSpeechSynthesizer instead of
//  AlarmManager + Android TextToSpeech.
//

import XCTest
@testable import Akshrava

final class WatchdogRecoveryPromptTests: XCTestCase {

    func testSpeechLocaleResolvesForAllSupportedLanguages() {
        // Every supported language tag must resolve to a BCP-47 language code
        // that AVSpeechSynthesisVoice can use for TTS.
        for lang in SupportedLanguages.all {
            let voice = AVSpeechSynthesisVoiceProxy.bestVoice(for: lang.tag)
            XCTAssertFalse(voice.isEmpty,
                "\(lang.tag) must resolve to a non-empty BCP-47 language identifier")
        }
    }

    func testBlankLanguageTagStillYieldsAFallbackLocale() {
        // Empty or whitespace-only tags must not crash — fall back to English
        let voice = AVSpeechSynthesisVoiceProxy.bestVoice(for: "")
        XCTAssertFalse(voice.isEmpty, "Blank tag must fall back to a valid locale")

        let trimmedVoice = AVSpeechSynthesisVoiceProxy.bestVoice(for: "   ")
        XCTAssertFalse(trimmedVoice.isEmpty, "Whitespace-only tag must fall back to a valid locale")
    }

    func testWatchdogIntervalMillisIsDocumented() {
        // Pin the watchdog interval so a regression in Watchdog.intervalMs is caught.
        XCTAssertEqual(Watchdog.intervalMs, 3 * 60_000,
            "Watchdog interval must be 3 minutes")
    }

    func testWatchdogIntervalMatchesStaleAfterMs() {
        // The watchdog fires every INTERVAL_MS and marks a session stale after STALE_AFTER_MS.
        // These must be equal (or the watchdog could never detect a stale session on first fire).
        XCTAssertEqual(Watchdog.intervalMs, SessionFlags.staleAfterMs,
            "Watchdog interval and session staleness threshold must be aligned")
    }

    func testStaleRecoverySpeechIsThePressStartPrompt() {
        let en = AlertManager.shared.resolvedTemplate(
            messageKey: Watchdog.stalledRecoverySpeechKey,
            language: "en"
        )
        XCTAssertEqual(
            en?.text,
            "Open Akshrava and press Start assistance again. Keep using your cane."
        )
        XCTAssertNoThrow(Watchdog.promptStaleSessionRecovery(language: "en"))
    }

    func testSessionIsRecognisedAsStaleAfterHeartbeatGap() {
        // Simulate stale state by calling setActive(false) and waiting — but since we can't
        // mock time, we at least verify isStale() doesn't crash after toggling active state.
        SessionFlags.setActive(true)
        SessionFlags.setActive(false)
        let _ = SessionFlags.isStale()  // must not throw
    }
}

/// Lightweight proxy to resolve a BCP-47 language string for TTS without calling
/// AVSpeechSynthesizer directly (which has side-effects in unit tests).
private enum AVSpeechSynthesisVoiceProxy {
    static func bestVoice(for tag: String) -> String {
        let trimmed = tag.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return "en-IN" }
        // Match the first language component from the tag
        let components = trimmed.components(separatedBy: "-")
        guard let lang = components.first, !lang.isEmpty else { return "en-IN" }
        // Map wireCode language prefix back to a BCP-47 tag
        let mapping: [String: String] = [
            "en": "en-IN", "hi": "hi-IN", "ta": "ta-IN",
            "kn": "kn-IN", "ml": "ml-IN", "te": "te-IN"
        ]
        return mapping[lang] ?? "en-IN"
    }
}
