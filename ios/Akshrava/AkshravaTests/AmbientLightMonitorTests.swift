//
//  AmbientLightMonitorTests.swift
//  AkshravaTests
//

import XCTest
@testable import Akshrava

final class AmbientLightMonitorTests: XCTestCase {

    func testClassifyHysteresisBand() {
        XCTAssertEqual(AmbientLightMonitor.classify(lux: 5), .dark)
        XCTAssertEqual(AmbientLightMonitor.classify(lux: 80), .bright)
        XCTAssertNil(AmbientLightMonitor.classify(lux: 30))
    }

    func testFirstConfidentReadingSeedsWithoutAnnounce() {
        let step = AmbientLightMonitor.step(state: AmbientLightState(), lux: 5, nowMs: 1_000)
        XCTAssertEqual(step.state.established, .dark)
        XCTAssertNil(step.announce)
    }

    func testEdgeRequiresHoldThenAnnounces() {
        var state = AmbientLightState(established: .bright)
        var step = AmbientLightMonitor.step(state: state, lux: 5, nowMs: 1_000)
        XCTAssertNil(step.announce)
        state = step.state
        step = AmbientLightMonitor.step(state: state, lux: 5, nowMs: 1_000 + AmbientLightMonitor.holdMs)
        XCTAssertEqual(step.announce, .dark)
        XCTAssertEqual(step.state.established, .dark)
    }

    func testCooldownSuppressesRepeatAnnounce() {
        let state = AmbientLightState(established: .bright, lastAnnounceMs: 5_000)
        let step = AmbientLightMonitor.step(
            state: state,
            lux: 5,
            nowMs: 5_000 + AmbientLightMonitor.holdMs
        )
        // Hold satisfied on first differing sample only if candidateSince was set — seed candidate first.
        XCTAssertNil(step.announce)
        let held = AmbientLightMonitor.step(
            state: AmbientLightState(
                established: .bright,
                candidate: .dark,
                candidateSinceMs: 5_000,
                lastAnnounceMs: 5_000
            ),
            lux: 5,
            nowMs: 5_000 + AmbientLightMonitor.holdMs
        )
        XCTAssertNil(held.announce)
        XCTAssertEqual(held.state.established, .dark)
    }

    func testStatusStringsAreAwarenessOnly() {
        XCTAssertEqual(AmbientLightMonitor.statusText(for: .dark), "Environment is dark.")
        XCTAssertEqual(AmbientLightMonitor.statusText(for: .bright), "Brighter now.")
        XCTAssertFalse(AmbientLightMonitor.statusText(for: .dark).lowercased().contains("safe"))
    }

    func testApproximateLuxMapsHighISOTowardDark() {
        let darkish = AmbientLightMonitor.approximateLux(iso: 800, exposureSeconds: 0.003)
        let brightish = AmbientLightMonitor.approximateLux(iso: 50, exposureSeconds: 0.001)
        XCTAssertLessThan(darkish, brightish)
    }
}
