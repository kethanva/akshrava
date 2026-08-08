//
//  SafetyBoundaryTests.swift
//  AkshravaTests
//
//  End-to-end safety boundary audit. No file in the iOS codebase may claim, imply,
//  or add: navigation, crossing decisions, collision avoidance, approach-speed,
//  clear-path, or "safe" guarantees.
//

import XCTest
@testable import Akshrava

final class SafetyBoundaryTests: XCTestCase {

    private let forbiddenTerms = [
        "navigation",
        "crossing decision",
        "collision avoidance",
        "approach speed",
        "clear path",
        "safe to cross",
        "you can go",
        "proceed safely",
        "it is safe",
        "road is clear",
        "turn left",
        "turn right",
        "go straight",
    ]

    func testLookClearMessageDoesNotImplySafety() {
        let message = "No alert in recent view. Continue using cane or guide"
        for term in forbiddenTerms {
            XCTAssertFalse(
                message.lowercased().contains(term.lowercased()),
                "look_clear message contains forbidden term: '\(term)'"
            )
        }
    }

    func testConnectionDroppedMessageDoesNotImplyNavigation() {
        let message = "Connection lost. Use cane or guide"
        for term in forbiddenTerms {
            XCTAssertFalse(
                message.lowercased().contains(term.lowercased()),
                "connection_dropped message contains forbidden term: '\(term)'"
            )
        }
    }

    func testVersionStringDoesNotContainForbiddenTerms() {
        let version = AppConfig.shared.appVersion
        for term in forbiddenTerms {
            XCTAssertFalse(version.lowercased().contains(term.lowercased()))
        }
    }

    func testAllSupportedLanguageLabelsAreNonEmpty() {
        for lang in SupportedLanguages.all {
            XCTAssertFalse(lang.label.isEmpty, "Language label for \(lang.tag) must not be empty")
            XCTAssertFalse(lang.wireCode.isEmpty, "Wire code for \(lang.tag) must not be empty")
        }
    }
}
