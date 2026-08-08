//
//  CapturePolicyTests.swift
//  AkshravaTests
//
//  Mirrors CapturePolicyTest.kt — validates motion-adaptive capture rate gating.
//

import XCTest
@testable import Akshrava

final class CapturePolicyTests: XCTestCase {

    func testDoesNotCaptureWhenIntervalNotElapsed() {
        let policy = CapturePolicy()
        let now: Int64 = 1_000
        XCTAssertTrue(policy.shouldCapture(isMoving: true, currentMonoMs: now))
        // Immediately after — interval not elapsed
        XCTAssertFalse(policy.shouldCapture(isMoving: true, currentMonoMs: now + 100))
    }

    func testCapturesWhenMovingIntervalElapsed() {
        let policy = CapturePolicy()
        let now: Int64 = 1_000
        _ = policy.shouldCapture(isMoving: true, currentMonoMs: now)
        // 500ms+ later when moving
        XCTAssertTrue(policy.shouldCapture(isMoving: true, currentMonoMs: now + 600))
    }

    func testStationaryIntervalIsLongerThanMovingInterval() {
        let policy = CapturePolicy()
        let now: Int64 = 1_000
        _ = policy.shouldCapture(isMoving: false, currentMonoMs: now)
        // 500ms later — still within stationary interval (5000ms)
        XCTAssertFalse(policy.shouldCapture(isMoving: false, currentMonoMs: now + 600))
    }

    func testStationaryCapturesAfter5Seconds() {
        let policy = CapturePolicy()
        let now: Int64 = 1_000
        _ = policy.shouldCapture(isMoving: false, currentMonoMs: now)
        XCTAssertTrue(policy.shouldCapture(isMoving: false, currentMonoMs: now + 5001))
    }

    func testResetAllowsImmediateCaptureAgain() {
        let policy = CapturePolicy()
        let now: Int64 = 1_000
        _ = policy.shouldCapture(isMoving: true, currentMonoMs: now)
        policy.reset()
        XCTAssertTrue(policy.shouldCapture(isMoving: true, currentMonoMs: now + 1))
    }

    func testFirstCaptureAlwaysPasses() {
        let policy = CapturePolicy()
        XCTAssertTrue(policy.shouldCapture(isMoving: false, currentMonoMs: 0))
    }
}
