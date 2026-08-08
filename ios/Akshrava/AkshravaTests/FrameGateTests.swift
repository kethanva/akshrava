//
//  FrameGateTests.swift
//  AkshravaTests
//

import XCTest
@testable import Akshrava

final class FrameGateTests: XCTestCase {

    func testFrameGateCanBeInstantiated() {
        XCTAssertNotNil(FrameGate())
    }

    func testDropResultsAreDistinct() {
        XCTAssertNotEqual(FrameGateResult.pass, FrameGateResult.occluded)
        XCTAssertNotEqual(FrameGateResult.pass, FrameGateResult.glared)
    }

    func testBlurAnnounceRequiresConsecutiveEvidence() {
        XCTAssertFalse(FrameGate.shouldAnnounceBlur(nowMs: 1_000, consecutiveBlurredFrames: 4, lastAnnounceMs: nil))
        XCTAssertTrue(FrameGate.shouldAnnounceBlur(nowMs: 1_000, consecutiveBlurredFrames: 5, lastAnnounceMs: nil))
    }

    func testBlurAnnounceRespectsCooldownAndNullLast() {
        XCTAssertFalse(
            FrameGate.shouldAnnounceBlur(
                nowMs: 10_000,
                consecutiveBlurredFrames: 5,
                lastAnnounceMs: 9_000
            )
        )
        XCTAssertTrue(
            FrameGate.shouldAnnounceBlur(
                nowMs: 10_000 + FrameGate.blurAnnounceCooldownMs,
                consecutiveBlurredFrames: 5,
                lastAnnounceMs: 10_000
            )
        )
    }
}
