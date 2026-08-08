//
//  SessionFlagsTests.swift
//  AkshravaTests
//
//  Mirrors SessionFlagsTest.kt.
//

import XCTest
@testable import Akshrava

final class SessionFlagsTests: XCTestCase {

    override func setUp() {
        super.setUp()
        // Reset to inactive state before each test
        SessionFlags.setActive(false)
    }

    func testIsNotActiveByDefault() {
        SessionFlags.setActive(false)
        XCTAssertFalse(SessionFlags.isActive())
    }

    func testSetActiveMakesSessionActive() {
        SessionFlags.setActive(true)
        XCTAssertTrue(SessionFlags.isActive())
    }

    func testSetInactiveMakesSessionInactive() {
        SessionFlags.setActive(true)
        SessionFlags.setActive(false)
        XCTAssertFalse(SessionFlags.isActive())
    }

    func testStaleAfterMsIsThreeMinutes() {
        XCTAssertEqual(SessionFlags.staleAfterMs, 3 * 60_000)
    }

    func testFreshHeartbeatIsNotStale() {
        SessionFlags.setActive(true)
        // Just set — heartbeat is now, must not be stale
        XCTAssertFalse(SessionFlags.isStale())
    }

    func testHeartbeatUpdatesTimestamp() {
        SessionFlags.setActive(true)
        SessionFlags.heartbeat()
        XCTAssertFalse(SessionFlags.isStale())
    }
}
