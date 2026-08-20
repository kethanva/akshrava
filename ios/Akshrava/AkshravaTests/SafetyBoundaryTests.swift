//
//  SafetyBoundaryTests.swift
//  AkshravaTests
//
//  End-to-end safety boundary audit. No file in the iOS codebase may claim, imply,
//  or add: navigation, crossing decisions, collision avoidance, approach-speed,
//  clear-path, or "safe" guarantees.
//
//  These tests audit ACTUAL runtime-producible spoken text -- AlertManager's template dictionary
//  and AssistSessionManager's result-to-speech selector -- rather than string literals copied
//  into this file. A copied literal passes forever regardless of what the app actually says,
//  which is coverage theatre: it was previously possible to introduce a forbidden phrase into a
//  real template and have this suite stay green.
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

    private func assertNoForbiddenTerm(_ text: String, context: String) {
        let lowered = text.lowercased()
        for term in forbiddenTerms {
            XCTAssertFalse(
                lowered.contains(term.lowercased()),
                "\(context) contains forbidden term '\(term)': \"\(text)\""
            )
        }
    }

    func testAllAlertManagerTemplateStringsAreFreeOfForbiddenTerms() {
        let strings = AlertManager.shared.allTemplateStrings()
        XCTAssertFalse(strings.isEmpty, "Template audit found nothing to check -- likely broken, not passing")
        for text in strings {
            assertNoForbiddenTerm(text, context: "AlertManager template")
        }
    }

    func testSpeechTextForHazardResultsIsFreeOfForbiddenTerms() {
        // Exercise the actual selector every live hazard result flows through, across the shapes
        // it branches on: server-provided spoken_preview, message_key fallback, and a look/priority
        // response using look_summary.
        let payloads: [[String: Any]] = [
            [
                "hazard": [
                    "level": "urgent",
                    "spoken_preview": "Vehicle nearby",
                    "message_key": "vehicle_nearby",
                    "bearing": "ahead",
                ],
            ],
            [
                "hazard": [
                    "level": "advisory",
                    "spoken_preview": "Person ahead",
                    "message_key": "person_ahead",
                ],
            ],
            [
                "priority": true,
                "look_summary": "No alert in recent view. Continue using cane or guide",
            ],
        ]
        for payload in payloads {
            if let spoken = AssistSessionManager.speechText(forResult: payload) {
                assertNoForbiddenTerm(spoken.text, context: "speechText(forResult:) output")
            }
        }
    }

    func testAMessageKeyIsNeverReturnedAsSpeechText() {
        // A message_key is a protocol lookup token, not speech. Returning it as text made the
        // synthesizer say "vehicle_nearby" verbatim to the user. When the server sends no
        // spoken_preview, the selector must decline and let the caller resolve the key through
        // AlertManager's templates instead.
        let keyOnly: [String: Any] = [
            "hazard": ["level": "urgent", "message_key": "vehicle_nearby"],
        ]
        XCTAssertNil(
            AssistSessionManager.speechText(forResult: keyOnly),
            "A hazard carrying only a message_key must not yield speech text"
        )

        // A look answer with neither a summary nor a preview must also stay silent rather than
        // invent reassurance -- the safety boundary forbids implying a clear view.
        let emptyLook: [String: Any] = ["priority": true]
        XCTAssertNil(AssistSessionManager.speechText(forResult: emptyLook))
    }

    func testSpeechTextRejectsForbiddenPrefixesAndReturnsNil() {
        let payloads: [[String: Any]] = [
            ["priority": true, "look_summary": "It is safe to proceed"],
            ["priority": true, "look_summary": "The path is clear ahead"],
            ["priority": true, "look_summary": "Do not cross yet"],
            ["priority": true, "look_summary": "Start navigating now"],
            ["priority": true, "look_summary": "Collision likely"],
            ["priority": true, "look_summary": "Vehicle approaching"],
            ["priority": true, "look_summary": "Walk safely"],
        ]
        for payload in payloads {
            XCTAssertNil(
                AssistSessionManager.speechText(forResult: payload),
                "forbidden look_summary must not become speech: \(payload)"
            )
        }
        let allowed: [String: Any] = [
            "priority": true,
            "look_summary": "No alert in this recent view. Continue using cane or guide",
        ]
        XCTAssertNotNil(AssistSessionManager.speechText(forResult: allowed))
    }

    func testRejectedLookIsFlaggedSoCallerDoesNotSpeakTheHazard() {
        let rejected: [String: Any] = [
            "priority": true,
            "look_summary": "It is safe to proceed",
            "hazard": ["message_key": "vehicle_nearby", "spoken_preview": "Vehicle nearby"],
        ]
        XCTAssertNil(AssistSessionManager.speechText(forResult: rejected))
        XCTAssertTrue(AssistSessionManager.lookWasRejected(rejected))
        let allowedLook: [String: Any] = [
            "priority": true,
            "look_summary": "Person ahead",
        ]
        XCTAssertFalse(AssistSessionManager.lookWasRejected(allowedLook))
    }

    func testRejectedLookUsesSpokenPreviewWhenLookSummaryIsBlank() {
        let rejected: [String: Any] = [
            "priority": true,
            "look_summary": "",
            "hazard": [
                "message_key": "vehicle_nearby",
                "spoken_preview": "It is safe to proceed",
            ],
        ]
        XCTAssertNil(AssistSessionManager.speechText(forResult: rejected))
        XCTAssertTrue(AssistSessionManager.lookWasRejected(rejected))
    }

    func testDenyListPrefixesMatchTheAndroidClient() {
        XCTAssertEqual(
            AssistSessionManager.forbiddenAwarenessPrefixes,
            ["saf", "clear", "cross", "navigat", "collis", "approach"]
        )
    }

    func testVersionStringDoesNotContainForbiddenTerms() {
        assertNoForbiddenTerm(AppConfig.shared.appVersion, context: "AppConfig.appVersion")
    }

    func testAllSupportedLanguageLabelsAreNonEmpty() {
        for lang in SupportedLanguages.all {
            XCTAssertFalse(lang.label.isEmpty, "Language label for \(lang.tag) must not be empty")
            XCTAssertFalse(lang.wireCode.isEmpty, "Wire code for \(lang.tag) must not be empty")
        }
    }
}
