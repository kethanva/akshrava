//
//  ProtocolClientTests.swift
//  AkshravaTests
//

import XCTest
@testable import Akshrava

final class ProtocolClientTests: XCTestCase {

    func testLegacyPoseCdegFloorIsNegative9000() {
        XCTAssertEqual(ProtocolClient.legacyPoseCdegFloor, -9000)
    }

    func testPoseBelowLegacyFloorIsOmittedByDefault() {
        XCTAssertNil(ProtocolClient.wirePoseCdeg(-12_000))
        XCTAssertNil(ProtocolClient.wirePoseCdeg(-9_001))
    }

    func testPoseAtOrAboveLegacyFloorIsPassedThrough() {
        XCTAssertEqual(ProtocolClient.wirePoseCdeg(-9_000), -9_000)
        XCTAssertEqual(ProtocolClient.wirePoseCdeg(-8_000), -8_000)
    }

    func testFullPoseRangePassesExtremeValues() {
        XCTAssertEqual(ProtocolClient.wirePoseCdeg(-12_000, serverAcceptsFullPoseRange: true), -12_000)
        XCTAssertEqual(ProtocolClient.wirePoseCdeg(17_000, serverAcceptsFullPoseRange: true), 17_000)
    }

    func testBuildFrameHeaderUsesWireContractFields() {
        let frame = EncodedFrameWire(jpegData: Data([0xFF, 0xD8]), width: 640, height: 480)
        let header = ProtocolClient.buildFrameHeader(
            frameId: 7,
            captureMonoMs: 1000,
            captureEpochMs: 2_000,
            frame: frame,
            calibrationId: "calib-1",
            language: "en-IN",
            pitchCdeg: -8_000,
            rollCdeg: -12_000,
            poseAgeMs: 20,
            serverAcceptsFullPoseRange: false
        )
        XCTAssertEqual(header["type"] as? String, "frame")
        XCTAssertEqual((header["id"] as? NSNumber)?.int64Value, 7)
        XCTAssertEqual(header["w"] as? Int, 640)
        XCTAssertEqual(header["h"] as? Int, 480)
        XCTAssertEqual(header["jpeg_bytes"] as? Int, 2)
        XCTAssertEqual(header["camera_calibration_id"] as? String, "calib-1")
        XCTAssertEqual(header["language"] as? String, "en")
        XCTAssertEqual(header["pitch_cdeg"] as? Int, -8_000)
        // Roll below legacy floor is omitted against an old server.
        XCTAssertNil(header["roll_cdeg"])
        XCTAssertEqual(header["result_acknowledgement"] as? Bool, true)
    }

    func testParseCapabilitiesToleratesMissingOrMalformed() {
        XCTAssertTrue(ProtocolClient.parseCapabilities([:]).isEmpty)
        XCTAssertEqual(
            ProtocolClient.parseCapabilities(["capabilities": ["pose_cdeg_full_range", "result_acknowledgement"]]),
            Set([ProtocolClient.capabilityPoseCdegFullRange, ProtocolClient.capabilityResultAcknowledgement])
        )
    }

    func testConnectWithoutTokenFailsClosed() {
        let client = ProtocolClient()
        let url = URL(string: "wss://example.invalid/v1/session")!
        XCTAssertNoThrow(client.connect(url: url, authToken: "   "))
        XCTAssertFalse(client.canStream())
    }

    func testStaleInferenceTickPeriodMatchesAndroid() {
        XCTAssertEqual(ProtocolClient.staleInferenceTickPeriodMs, 2_000)
        XCTAssertEqual(ProtocolClient.urgentFreshnessMs, 1_500)
        XCTAssertEqual(ProtocolClient.lookFreshnessMs, 2_500)
    }

    func testAlertMaxAgeMsIs2500() {
        XCTAssertEqual(AppConfig.shared.alertMaxAgeMs, 2500)
        XCTAssertEqual(ProtocolClient.staleAlertMs, 2500)
    }

    func testDisconnectWithoutConnectingDoesNotCrash() {
        XCTAssertNoThrow(ProtocolClient().disconnect())
    }

    func testLateFrameCannotSettleNewerInFlightSlot() {
        XCTAssertTrue(ProtocolClient.frameMaySettle(inFlightFrameId: 12, receivedFrameId: 12))
        XCTAssertFalse(ProtocolClient.frameMaySettle(inFlightFrameId: 13, receivedFrameId: 12))
        XCTAssertFalse(ProtocolClient.frameMaySettle(inFlightFrameId: nil, receivedFrameId: 12))
    }

    // ---- reconnect backoff (H3) ----
    //
    // A fixed 2s retry made every phone in the fleet retry in lockstep during a Cloud Run
    // rollout. Backoff must grow with attempt count, cap, and never be so jittered it could
    // exceed the cap or go negative.

    func testReconnectDelayGrowsWithAttemptAndCaps() {
        // random=1.0 pins full jitter to its ceiling, isolating the growth/cap curve.
        let d1 = ProtocolClient.reconnectDelaySeconds(attempt: 1, random: 1.0)
        let d2 = ProtocolClient.reconnectDelaySeconds(attempt: 2, random: 1.0)
        let d3 = ProtocolClient.reconnectDelaySeconds(attempt: 3, random: 1.0)
        XCTAssertEqual(d1, 1.0, accuracy: 0.001)
        XCTAssertEqual(d2, 2.0, accuracy: 0.001)
        XCTAssertEqual(d3, 4.0, accuracy: 0.001)

        let huge = ProtocolClient.reconnectDelaySeconds(attempt: 100, random: 1.0)
        XCTAssertEqual(huge, ProtocolClient.reconnectMaxSeconds, accuracy: 0.001)
    }

    func testReconnectDelayJitterNeverExceedsCapOrGoesNegative() {
        for attempt in [1, 2, 5, 10, 50] {
            for random in [0.0, 0.25, 0.5, 0.75, 1.0] {
                let delay = ProtocolClient.reconnectDelaySeconds(attempt: attempt, random: random)
                XCTAssertGreaterThanOrEqual(delay, 0)
                XCTAssertLessThanOrEqual(delay, ProtocolClient.reconnectMaxSeconds)
            }
        }
    }

    // ---- terminal rejection via message body (C3) ----
    //
    // iOS's public URLSessionWebSocketTask.CloseCode enum cannot represent a close code outside
    // 1000-1015 -- a server close with code 4403 collapses to a generic "invalid" value with the
    // number discarded, so a revoked device could never be told apart from an ordinary transport
    // drop and would reconnect forever. The server sends a matching error message body as the
    // reliable channel; these codes are what the client treats as terminal.

    func testTerminalErrorCodesIncludeDeviceRevokedAndAuthFailed() {
        XCTAssertTrue(ProtocolClient.terminalErrorCodes.contains("device_revoked"))
        XCTAssertTrue(ProtocolClient.terminalErrorCodes.contains("authentication_failed"))
        XCTAssertTrue(ProtocolClient.terminalErrorCodes.contains("session_superseded"))
    }

    func testTerminalErrorCodesDoNotIncludeOrdinaryErrors() {
        XCTAssertFalse(ProtocolClient.terminalErrorCodes.contains("worker_saturated"))
        XCTAssertFalse(ProtocolClient.terminalErrorCodes.contains("invalid_frame_header"))
        XCTAssertFalse(ProtocolClient.terminalErrorCodes.contains("vision_unavailable"))
        XCTAssertFalse(ProtocolClient.terminalErrorCodes.contains("malformed_control_message"))
    }

    func testMalformedControlMessageIsASilentSoftError() {
        XCTAssertTrue(ProtocolClient.silentSoftErrorCodes.contains("malformed_control_message"))
        XCTAssertFalse(ProtocolClient.terminalErrorCodes.contains("malformed_control_message"))
    }

    func testSoftShedAnnouncementIsBounded() {
        XCTAssertFalse(ProtocolClient.shouldAnnounceSoftShed(
            consecutiveSoftSheds: 2, lastAnnounceAtMonoMs: 0, nowMonoMs: 1_000
        ))
        XCTAssertTrue(ProtocolClient.shouldAnnounceSoftShed(
            consecutiveSoftSheds: 3, lastAnnounceAtMonoMs: 0, nowMonoMs: 1_000
        ))
        XCTAssertFalse(ProtocolClient.shouldAnnounceSoftShed(
            consecutiveSoftSheds: 4, lastAnnounceAtMonoMs: 1_000, nowMonoMs: 10_000
        ))
        XCTAssertTrue(ProtocolClient.shouldAnnounceSoftShed(
            consecutiveSoftSheds: 4, lastAnnounceAtMonoMs: 1_000, nowMonoMs: 16_000
        ))
    }

    func testTransportDropTreats4409AsTerminal() {
        XCTAssertTrue(ProtocolClient.terminalErrorCodes.contains("session_superseded"))
        XCTAssertFalse(ProtocolClient.silentSoftErrorCodes.contains("session_superseded"))
    }

    func testTransientAndSustainedInferenceFailuresHaveDifferentPolicies() {
        XCTAssertTrue(ProtocolClient.silentSoftErrorCodes.contains("worker_saturated"))
        XCTAssertTrue(ProtocolClient.silentSoftErrorCodes.contains("frame_in_flight"))
        XCTAssertTrue(ProtocolClient.silentSoftErrorCodes.contains("frame_rate_limited"))
        XCTAssertFalse(ProtocolClient.silentSoftErrorCodes.contains("inference_circuit_open"))
        XCTAssertTrue(ProtocolClient.isInferenceOutageError("inference_circuit_open"))
        XCTAssertFalse(ProtocolClient.isInferenceOutageError("worker_saturated"))
    }
}
