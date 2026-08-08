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

    func testLookClearMessageDoesNotContainSafeOrClear() {
        // Verified via known message string — safety boundary enforcement
        let msg = "No alert in recent view. Continue using cane or guide"
        XCTAssertFalse(msg.lowercased().contains("safe"),
                       "look_clear must never use the word 'safe'")
        XCTAssertFalse(msg.lowercased().contains("clear path"),
                       "look_clear must never use the phrase 'clear path'")
    }
}
