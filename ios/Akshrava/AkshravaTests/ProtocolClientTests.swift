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
}
