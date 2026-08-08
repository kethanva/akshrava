//
//  SessionDurationTests.swift
//  AkshravaTests
//
//  Timing invariants for a sustained walking session.
//  Mirrors Android SessionDurationTest.kt exactly.
//
//  Field reports: assistance appears to stop after about three minutes and needs
//  a manual Stop/Start. Three minutes is exactly Watchdog.intervalMs and
//  SessionFlags.staleAfterMs. These tests pin the relationships between the timers
//  that decide whether a healthy session is mistaken for a dead one.
//

import XCTest
@testable import Akshrava

final class SessionDurationTests: XCTestCase {
    private static let targetSessionMs: Int64 = 15 * 60_000

    func testHeartbeatIsFrequentEnoughThatAHealthySessionIsNeverCalledStale() {
        // A healthy session writes a heartbeat every heartbeatIntervalMs. That interval must
        // leave generous margin inside the stale window, or ordinary scheduling jitter produces
        // a spurious "assistance stopped" alarm mid-walk.
        XCTAssertLessThanOrEqual(
            AssistSessionConstants.heartbeatIntervalMs * 3,
            SessionFlags.staleAfterMs,
            "heartbeat interval must be well inside the stale window"
        )
    }

    func testRecoverableCameraStallHealsBeforeTheWatchdogCallsTheSessionDead() {
        // A stalled camera restarts after cameraStallRebindMs. That recovery must complete
        // well before the staleness threshold, or a self-healing hiccup escalates into a
        // spoken restart prompt the user cannot ignore.
        let worstCaseRecoveryMs =
            AssistSessionConstants.cameraStallRebindMs +
            AssistSessionConstants.cameraStallCheckMs +
            AssistSessionConstants.heartbeatIntervalMs
        XCTAssertLessThanOrEqual(
            worstCaseRecoveryMs * 2,
            SessionFlags.staleAfterMs,
            "camera stall recovery (\(worstCaseRecoveryMs) ms) must finish inside the stale window (\(SessionFlags.staleAfterMs) ms)"
        )
    }

    func testStallDetectorPollsSeveralTimesPerRebindWindow() {
        XCTAssertLessThanOrEqual(
            AssistSessionConstants.cameraStallCheckMs * 2,
            AssistSessionConstants.cameraStallRebindMs,
            "stall check must sample several times per rebind window to react promptly"
        )
    }

    func testKeepAliveOutlastsATargetLengthSession() {
        XCTAssertGreaterThanOrEqual(
            AssistSessionConstants.keepAliveDurationMs,
            SessionDurationTests.targetSessionMs * 2,
            "keep-alive (\(AssistSessionConstants.keepAliveDurationMs) ms) must outlast a \(SessionDurationTests.targetSessionMs) ms session"
        )
    }

    func testKeepAlivesAreRenewedFarInsideTheirOwnDuration() {
        XCTAssertLessThanOrEqual(
            AssistSessionConstants.keepAliveRenewIntervalMs * 2,
            AssistSessionConstants.keepAliveDurationMs,
            "renew interval must leave margin inside the \(AssistSessionConstants.keepAliveDurationMs) ms duration"
        )
        // Renewal rides on the heartbeat; the heartbeat must fire frequently enough to not miss a renewal.
        XCTAssertLessThanOrEqual(
            AssistSessionConstants.heartbeatIntervalMs * 4,
            AssistSessionConstants.keepAliveRenewIntervalMs,
            "heartbeat must fire many times per renewal window"
        )
    }

    func testWatchdogKeepsCheckingForTheWholeSession() {
        let wakeUps = SessionDurationTests.targetSessionMs / Watchdog.intervalMs
        XCTAssertGreaterThanOrEqual(wakeUps, 4,
            "watchdog must wake repeatedly across a session, got \(wakeUps)")
    }

    func testAppPingKeepsTheServerAdmissionLeaseAlive() {
        // The server session lease is 180 s; pinging must happen several times per lease
        // so one dropped ping cannot evict a live walking session.
        let serverLeaseMs: Int64 = 180_000
        XCTAssertLessThanOrEqual(
            ProtocolClient.appPingIntervalMs * 3,
            serverLeaseMs,
            "app ping (\(ProtocolClient.appPingIntervalMs) ms) must renew well inside the \(serverLeaseMs) ms server lease"
        )
    }

    func testSettleTimeoutCannotOutlastTheStaleWindow() {
        let totalSettleMs = ProtocolClient.frameSettleTimeoutMs * Int64(ProtocolClient.settleTimeoutsBeforeReconnect)
        XCTAssertLessThan(
            totalSettleMs,
            SessionFlags.staleAfterMs,
            "settle timeout must resolve well inside the stale window"
        )
    }

    func testQualityDrivenCameraRebindsAreRateLimited() {
        XCTAssertGreaterThanOrEqual(
            AssistSessionConstants.minQualityRebindIntervalMs, 5_000,
            "quality rebinds must be spaced further apart than the frame cadence"
        )
        // Stall recovery bypasses the cooldown and must still act inside the stale window.
        XCTAssertLessThan(
            AssistSessionConstants.cameraStallRebindMs,
            SessionFlags.staleAfterMs,
            "stall recovery must remain far faster than the quality rebind cooldown"
        )
    }
}
