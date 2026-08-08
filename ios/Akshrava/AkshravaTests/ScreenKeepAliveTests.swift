//
//  ScreenKeepAliveTests.swift
//  AkshravaTests
//
//  Unit tests for ScreenKeepAlive — mirrors Android ScreenKeepAliveTest.kt.
//

import XCTest
@testable import Akshrava

final class ScreenKeepAliveTests: XCTestCase {

    func testScreenKeepAliveInitialisesWithNoMode() {
        let keepAlive = ScreenKeepAlive()
        XCTAssertEqual(keepAlive.mode, .none)
    }

    func testStartAndStopLifecycle() {
        let keepAlive = ScreenKeepAlive()
        let started = keepAlive.start()
        XCTAssertTrue(started, "ScreenKeepAlive.start() must succeed")
        XCTAssertEqual(keepAlive.mode, .idleTimerDisabled)
        XCTAssertTrue(keepAlive.isHoldingScreenOn())

        keepAlive.stop()
        XCTAssertEqual(keepAlive.mode, .none)
        XCTAssertFalse(keepAlive.isHoldingScreenOn())
    }

    func testDoubleStartIsIdempotent() {
        let keepAlive = ScreenKeepAlive()
        _ = keepAlive.start()
        let secondStart = keepAlive.start()
        XCTAssertTrue(secondStart, "Second start must be idempotent (still holding)")
        keepAlive.stop()
    }

    func testRenewDoesNotCrash() {
        let keepAlive = ScreenKeepAlive()
        // renew() is a no-op on iOS; must not crash
        keepAlive.renew()
        _ = keepAlive.start()
        keepAlive.renew()
        keepAlive.stop()
    }

    func testKeepAliveDurationConstantOutlastsTypicalSession() {
        // Keep-alive must last at least 15 minutes × 2 without renewal
        let targetSessionMs: Int64 = 15 * 60_000
        XCTAssertGreaterThanOrEqual(
            AssistSessionConstants.keepAliveDurationMs, targetSessionMs * 2,
            "Keep-alive duration must outlast a target-length session"
        )
    }

    func testRenewalIntervalIsWellInsideKeepAliveDuration() {
        XCTAssertLessThanOrEqual(
            AssistSessionConstants.keepAliveRenewIntervalMs * 2,
            AssistSessionConstants.keepAliveDurationMs,
            "Renewal interval must leave margin inside the duration"
        )
    }
}
