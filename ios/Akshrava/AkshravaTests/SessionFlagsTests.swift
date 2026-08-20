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

    func testOldHeartbeatAcrossSimulatedRebootIsStale() {
        // M5 regression: the heartbeat used to be stored as ProcessInfo.systemUptime, which
        // resets to near-zero on every device reboot. A session killed by a reboot would have
        // written a LARGE uptime value just before going down; a watchdog running after reboot
        // sees a small current uptime, so `current - last` goes negative and reads as "not stale"
        // -- a definitely-dead session reporting healthy. Simulate that shape directly against
        // the real storage this class uses (suite/key names mirror SessionFlags.swift) and assert
        // it is now correctly stale: wall-clock time keeps moving forward through a reboot, so an
        // implausibly-old absolute timestamp is always caught regardless of what rebooted.
        let suite = UserDefaults(suiteName: "org.akshrava.ios.prefs")!
        suite.set(true, forKey: "session_active")
        suite.set(1, forKey: "heartbeat_ms") // 1ms after the Unix epoch: always in the deep past
        XCTAssertTrue(SessionFlags.isStale())
    }

    func testFutureHeartbeatAfterClockCorrectionFailsTowardRecovery() {
        let suite = UserDefaults(suiteName: "org.akshrava.ios.prefs")!
        suite.set(true, forKey: "session_active")
        suite.set(
            Int64(Date().timeIntervalSince1970 * 1000) + 24 * 60 * 60 * 1000,
            forKey: "heartbeat_ms"
        )
        XCTAssertTrue(SessionFlags.isStale())
    }
}
