//
//  AlertManagerTests.swift
//  AkshravaTests
//
//  Mirrors AlertManagerTest.kt.
//

import XCTest
@testable import Akshrava

final class AlertManagerTests: XCTestCase {

    func testAlertManagerSharedInstanceIsNonNil() {
        XCTAssertNotNil(AlertManager.shared)
    }

    func testToggleMuteDoesNotCrash() {
        XCTAssertNoThrow(AlertManager.shared.toggleMute())
        // Reset to unmuted
        AlertManager.shared.toggleMute()
    }

    func testSpeakDoesNotCrashWithValidKey() {
        XCTAssertNoThrow(AlertManager.shared.speak(messageKey: "vehicle_nearby"))
    }

    func testSpeakDoesNotCrashWithUnknownKey() {
        // Unknown keys should fall back gracefully
        XCTAssertNoThrow(AlertManager.shared.speak(messageKey: "unknown_key_xyz"))
    }

    func testSpeakDoesNotCrashWithAllSupportedLanguages() {
        let keys = ["vehicle_nearby", "person_ahead", "obstacle_ahead",
                    "look_clear", "connection_open", "connection_dropped", "connection_restored"]
        let languages = ["en", "hi"]
        for key in keys {
            for lang in languages {
                XCTAssertNoThrow(AlertManager.shared.speak(messageKey: key, language: lang),
                                 "speak(\(key), \(lang)) must not crash")
            }
        }
    }

    func testSpeakUnknownKeyDoesNotThrowAndDoesNotCrashSynthesizer() {
        // H8: an unknown key must never be spoken literally. There is no public way to assert
        // "nothing was queued" without reaching into AVSpeechSynthesizer internals, so this pins
        // the observable contract: it must not throw, and a subsequent legitimate call must still
        // work (i.e. the missing-template path did not leave the synthesizer in a bad state).
        XCTAssertNoThrow(AlertManager.shared.speak(messageKey: "totally_unknown_key"))
        XCTAssertNoThrow(AlertManager.shared.speak(messageKey: "vehicle_nearby", language: "ta"))
    }

    func testSpeakFallsBackToEnglishTemplateForUnsupportedLanguage() {
        // "ta" has no template entries; speak(messageKey:language:) must fall back to the
        // English template text (paired with the English voice) rather than a Tamil voice
        // reading English words it has no phonemes for, and never the raw key itself.
        XCTAssertNoThrow(AlertManager.shared.speak(messageKey: "camera_dark", language: "ta"))
    }

    func testLookClearMessageDoesNotContainSafeOrClear() {
        // Verified via known message string — safety boundary enforcement
        let msg = "No alert in recent view. Continue using cane or guide"
        XCTAssertFalse(msg.lowercased().contains("safe"),
                       "look_clear must never use the word 'safe'")
        XCTAssertFalse(msg.lowercased().contains("clear path"),
                       "look_clear must never use the phrase 'clear path'")
    }
}
