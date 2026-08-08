//
//  EngineRebuildPolicyTests.swift
//  AkshravaTests
//
//  TTS engine rebuild policy tests — mirrors Android EngineRebuildPolicyTest.kt.
//
//  Live-reproduced failure: force-stopping the TTS engine leaves the client permanently
//  unbound. The old code swallowed the error, going silently mute until a manual Stop/Start.
//  These tests pin the recovery gate: rebuilds allowed after engine death, rate-limited, and
//  replenished on success — so a broken engine cannot become a rebuild storm.
//

import XCTest
@testable import Akshrava

final class EngineRebuildPolicyTests: XCTestCase {

    func testFirstFailureTriggersImmediateRebuild() {
        // lastRebuildMs == 0 means "never rebuilt": first engine death must recover at once.
        XCTAssertTrue(
            AlertManager.engineRebuildAllowed(nowMs: 5_000, lastRebuildMs: 0, streak: 0)
        )
    }

    func testRebuildsAreRateLimited() {
        let first: Int64 = 10_000
        XCTAssertFalse(
            AlertManager.engineRebuildAllowed(
                nowMs: first + AlertManager.engineRebuildMinIntervalMs - 1,
                lastRebuildMs: first,
                streak: 1
            ),
            "A second rebuild inside the interval floor must be suppressed"
        )
        XCTAssertTrue(
            AlertManager.engineRebuildAllowed(
                nowMs: first + AlertManager.engineRebuildMinIntervalMs,
                lastRebuildMs: first,
                streak: 1
            ),
            "After the floor elapses the next rebuild may proceed"
        )
    }

    func testStreakExhaustionStopsTheRebuildLoop() {
        // A hard-broken engine must not loop forever; haptics remain after the quota is spent.
        XCTAssertFalse(
            AlertManager.engineRebuildAllowed(
                nowMs: 1_000_000,
                lastRebuildMs: 0,
                streak: AlertManager.engineRebuildMaxStreak
            )
        )
    }

    func testSuccessResetsTheStreakSoRecoveryWorksRepeatedly() {
        // An OEM that kills the engine every few minutes for a whole walk must be survivable.
        XCTAssertTrue(
            AlertManager.engineRebuildAllowed(
                nowMs: Int64(30 * 60_000),
                lastRebuildMs: Int64(25 * 60_000),
                streak: 0
            )
        )
    }

    func testQuotaCoversRepeatedEngineDeathsAcrossATargetSession() {
        // 15-minute walk with OEM kill cadence ~3 min: five deaths, each needing one rebuild.
        // Streak resets on success, so per-burst quota just needs to beat transient double-failures.
        XCTAssertGreaterThanOrEqual(AlertManager.engineRebuildMaxStreak, 2,
            "Quota must survive an engine that needs one warm-up attempt after a cold kill")
        XCTAssertLessThanOrEqual(AlertManager.engineRebuildMinIntervalMs, 10_000,
            "Interval floor must stay far below utterance cadence so recovery arrives in time")
    }
}
