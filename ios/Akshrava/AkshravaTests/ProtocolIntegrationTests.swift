//
//  ProtocolIntegrationTests.swift
//  AkshravaTests
//
//  Cross-component integration checks that do not need a live camera or WSS socket.
//  These pin the phone↔backend contract the Android client already enforces.
//

import XCTest
@testable import Akshrava

final class ProtocolIntegrationTests: XCTestCase {

    func testWireHeaderRoundTripFieldsMatchBackendContract() {
        let frame = EncodedFrameWire(jpegData: Data(repeating: 0xAB, count: 128), width: 640, height: 480)
        let header = ProtocolClient.buildFrameHeader(
            frameId: 42,
            captureMonoMs: 9_001,
            captureEpochMs: 1_700_000_000_000,
            frame: frame,
            calibrationId: "ios-calib",
            language: "hi-IN",
            mode: "priority",
            priority: true,
            pitchCdeg: -8_500,
            rollCdeg: -12_500,
            poseAgeMs: 40,
            serverAcceptsFullPoseRange: false
        )

        XCTAssertEqual(header["type"] as? String, "frame")
        XCTAssertEqual((header["id"] as? NSNumber)?.int64Value, 42)
        XCTAssertEqual((header["capture_mono_ms"] as? NSNumber)?.int64Value, 9_001)
        XCTAssertEqual(header["jpeg_bytes"] as? Int, 128)
        XCTAssertEqual(header["camera_calibration_id"] as? String, "ios-calib")
        XCTAssertEqual(header["language"] as? String, "hi")
        XCTAssertEqual(header["mode"] as? String, "priority")
        XCTAssertEqual(header["priority"] as? Bool, true)
        XCTAssertEqual(header["pitch_cdeg"] as? Int, -8_500)
        XCTAssertNil(header["roll_cdeg"], "Legacy servers must not receive roll below -9000")
        XCTAssertTrue(JSONSerialization.isValidJSONObject(header))
    }

    func testReadyCapabilitiesUnlockFullPoseOnSubsequentFrame() {
        let caps = ProtocolClient.parseCapabilities([
            "capabilities": [
                ProtocolClient.capabilityPoseCdegFullRange,
                ProtocolClient.capabilityResultAcknowledgement,
            ],
            "protocol_version": 1,
        ])
        XCTAssertTrue(caps.contains(ProtocolClient.capabilityPoseCdegFullRange))
        let wired = ProtocolClient.wirePoseCdeg(-12_500, serverAcceptsFullPoseRange: true)
        XCTAssertEqual(wired, -12_500)
    }

    func testFreshnessIntegrationWithEchoedCaptureMono() {
        // Server echoes capture_mono_ms; phone ages against its own mono clock.
        let capture: Int64 = 5_000
        let nowFresh: Int64 = 6_000
        let nowStale: Int64 = 9_000
        let ageFresh = ProtocolClient.resultAgeMs(captureMonoMs: capture, nowMs: nowFresh)
        let ageStale = ProtocolClient.resultAgeMs(captureMonoMs: capture, nowMs: nowStale)
        let maxAge = ProtocolClient.maxSpeakAgeMs(
            priority: false,
            isUrgent: false,
            configuredStaleAlertMs: ProtocolClient.staleAlertMs
        )
        XCTAssertLessThanOrEqual(ageFresh, maxAge)
        XCTAssertGreaterThan(ageStale, maxAge)
    }

    func testLookResultPrefersLookSummaryOverInventedClear() {
        let payload: [String: Any] = [
            "type": "result",
            "priority": true,
            "look_summary": "Vehicle nearby. Use cane or guide.",
            "hazard": [
                "spoken_preview": "Vehicle nearby",
                "severity": "S1",
                "level": "urgent",
            ],
        ]
        let spoken = AssistSessionManager.speechText(forResult: payload)
        XCTAssertEqual(spoken?.text, "Vehicle nearby. Use cane or guide.")
        XCTAssertEqual(spoken?.urgent, true)
    }

    func testLookWithoutSummaryFallsBackToHazardPreviewNotLocalClear() {
        let payload: [String: Any] = [
            "priority": NSNumber(value: true),
            "look_summary": "",
            "hazard": ["spoken_preview": "Person ahead", "severity": "S2"],
        ]
        let spoken = AssistSessionManager.speechText(forResult: payload)
        XCTAssertEqual(spoken?.text, "Person ahead")
        XCTAssertFalse(spoken?.text.lowercased().contains("clear") ?? true)
    }

    func testNormalHazardUsesSpokenPreview() {
        let payload: [String: Any] = [
            "priority": false,
            "hazard": [
                "spoken_preview": "Obstacle ahead",
                "message_key": "obstacle_ahead",
                "severity": "S2",
            ],
        ]
        let spoken = AssistSessionManager.speechText(forResult: payload)
        XCTAssertEqual(spoken?.text, "Obstacle ahead")
        XCTAssertEqual(spoken?.urgent, false)
    }

    func testEmptyResultProducesNoSpeech() {
        XCTAssertNil(AssistSessionManager.speechText(forResult: ["priority": false]))
        XCTAssertNil(AssistSessionManager.speechText(forResult: [
            "priority": true,
            "look_summary": "  ",
            "hazard": ["spoken_preview": ""],
        ]))
    }

    func testMessageKeyWithoutPreviewIsNotReturnedAsSpeech() {
        let spoken = AssistSessionManager.speechText(forResult: [
            "priority": false,
            "hazard": ["message_key": "vehicle_nearby", "severity": "S1"],
        ])
        XCTAssertNil(spoken)
    }

    func testConnectRejectsBlankTokenWithoutOpeningSocket() {
        let client = ProtocolClient()
        client.connect(url: URL(string: "wss://example.invalid/v1/session")!, authToken: "")
        XCTAssertFalse(client.canStream())
        XCTAssertFalse(client.isTerminal(), "Missing token is a soft reject, not a permanent auth failure")
    }
}
