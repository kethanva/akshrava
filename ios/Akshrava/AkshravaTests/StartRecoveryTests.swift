//
//  StartRecoveryTests.swift
//  AkshravaTests
//
//  Start-press recovery semantics — mirrors Android StartRecoveryTest.kt.
//
//  Duplicate START on a healthy session must be ignored (re-taps and OEM intent redelivery
//  were tearing down a live WSS mid-walk). But a terminally dead session (auth failure,
//  permanent error) must not be swallowed — pressing Start is the only recovery route.
//

import XCTest
@testable import Akshrava

final class StartRecoveryTests: XCTestCase {

    /// Mirrors the guard logic in AssistSessionManager.startSession().
    /// Returns true if a new start attempt should be IGNORED (session is already healthy).
    private func ignoresDuplicateStart(
        stopping: Bool,
        hasActiveSession: Bool,
        isTerminal: Bool
    ) -> Bool {
        let recoverable = !(hasActiveSession && isTerminal)
        return !stopping && recoverable && hasActiveSession
    }

    func testDuplicateStartOnHealthySessionIsIgnored() {
        XCTAssertTrue(
            ignoresDuplicateStart(stopping: false, hasActiveSession: true, isTerminal: false),
            "A re-tap on a live session must not tear down the WebSocket"
        )
    }

    func testStartRebuildsATerminallyDeadClient() {
        // Auth revoked / token rejected: session must not silently no-op — user needs recovery.
        XCTAssertFalse(
            ignoresDuplicateStart(stopping: false, hasActiveSession: true, isTerminal: true),
            "Start must rebuild when the session can never recover on its own"
        )
    }

    func testStartRebuildsHalfDeadSessions() {
        // Camera failure set isSessionActive = false; a Start press must rebuild.
        XCTAssertFalse(
            ignoresDuplicateStart(stopping: false, hasActiveSession: false, isTerminal: false)
        )
    }

    func testStartInterruptsAnInProgressStop() {
        XCTAssertFalse(
            ignoresDuplicateStart(stopping: true, hasActiveSession: true, isTerminal: false),
            "Start during teardown must rebuild, not no-op"
        )
    }
}
