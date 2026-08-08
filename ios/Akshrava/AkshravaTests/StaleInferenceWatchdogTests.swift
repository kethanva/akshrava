//
//  StaleInferenceWatchdogTests.swift
//  AkshravaTests
//
//  F-30 stale-inference earcon policy tests.
//  Mirrors Android StaleInferenceWatchdogTest.kt.
//
//  The regression guarded here is a beep that never stops: the tick used to key off
//  "time since the last result", which a healthy but slow-cadence session trips continuously.
//

import XCTest
@testable import Akshrava

final class StaleInferenceWatchdogTests: XCTestCase {

    func testTicksWhileAFrameIsOutstandingPastTheThreshold() {
        XCTAssertTrue(
            ProtocolClient.shouldTickStaleInference(
                inFlight: true,
                frameAgeMs: ProtocolClient.staleInferenceTickAfterMs,
                ticksAlready: 0
            )
        )
    }

    func testStaysSilentWhenNoFrameIsOutstanding() {
        // A slow capture cadence is not a stall. CapturePolicy legitimately reaches a 5 s
        // interval on low battery, far past the 3 s tick threshold.
        XCTAssertFalse(
            ProtocolClient.shouldTickStaleInference(
                inFlight: false,
                frameAgeMs: 5_000,
                ticksAlready: 0
            )
        )
    }

    func testStaysSilentBeforeTheThreshold() {
        XCTAssertFalse(
            ProtocolClient.shouldTickStaleInference(
                inFlight: true,
                frameAgeMs: ProtocolClient.staleInferenceTickAfterMs - 1,
                ticksAlready: 0
            )
        )
    }

    func testStopsAfterTheTickBudgetIsSpent() {
        let stuckForever = ProtocolClient.frameSettleTimeoutMs * 10
        XCTAssertTrue(
            ProtocolClient.shouldTickStaleInference(
                inFlight: true,
                frameAgeMs: stuckForever,
                ticksAlready: ProtocolClient.staleInferenceMaxTicks - 1
            )
        )
        XCTAssertFalse(
            ProtocolClient.shouldTickStaleInference(
                inFlight: true,
                frameAgeMs: stuckForever,
                ticksAlready: ProtocolClient.staleInferenceMaxTicks
            )
        )
    }

    func testTickBudgetIsExhaustedWellInsideTheSettleTimeout() {
        // The settle timeout is the real recovery path; the earcon only flags the wait.
        let lastTickAtMs = ProtocolClient.staleInferenceTickAfterMs +
            ProtocolClient.staleInferenceTickPeriodMs * Int64(ProtocolClient.staleInferenceMaxTicks - 1)
        XCTAssertLessThan(lastTickAtMs, ProtocolClient.frameSettleTimeoutMs,
            "All stale-inference ticks must exhaust before the settle timeout fires")
    }
}
