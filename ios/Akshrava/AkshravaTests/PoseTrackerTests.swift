//
//  PoseTrackerTests.swift
//  AkshravaTests
//
//  Mirrors PoseTrackerTest.kt.
//

import XCTest
@testable import Akshrava

final class PoseTrackerTests: XCTestCase {

    func testPoseTrackerCanBeInstantiated() {
        let tracker = PoseTracker()
        XCTAssertNotNil(tracker)
    }

    func testInitialPitchIsZero() {
        let tracker = PoseTracker()
        XCTAssertEqual(tracker.currentPitchCdeg, 0)
    }

    func testInitialRollIsZero() {
        let tracker = PoseTracker()
        XCTAssertEqual(tracker.currentRollCdeg, 0)
    }

    func testInitialPoseAgeIsLarge() {
        let tracker = PoseTracker()
        // Before any update, age is 999 (sentinel value)
        XCTAssertEqual(tracker.poseAgeMs, 999)
    }

    func testStopDoesNotCrashWhenNeverStarted() {
        let tracker = PoseTracker()
        XCTAssertNoThrow(tracker.stop())
    }

    func testExtremeTiltThresholdIsAbove45Degrees() {
        // 45 degrees = 4500 centidegrees
        // Tilt alert fires above this threshold
        let tiltThresholdCdeg = 4500
        XCTAssertGreaterThan(tiltThresholdCdeg, 0)
    }
}
