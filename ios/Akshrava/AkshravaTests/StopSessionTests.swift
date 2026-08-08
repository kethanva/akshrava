//
//  StopSessionTests.swift
//  AkshravaTests
//
//  Tests for session stop/teardown semantics — mirrors Android StopReceiverTest.kt.
//  On iOS there is no BroadcastReceiver; the stop action is handled by a URLSession
//  notification or background task expiry rather than an explicit StopReceiver intent.
//

import XCTest
@testable import Akshrava

final class StopSessionTests: XCTestCase {

    func testSessionFlagsSetInactiveSurvivesRoundTrip() {
        // Simulate an external stop signal marking the session inactive.
        SessionFlags.setActive(true)
        XCTAssertTrue(SessionFlags.isActive())

        SessionFlags.setActive(false)
        XCTAssertFalse(SessionFlags.isActive())
    }

    func testSessionFlagsAreIdempotentOnDoubleStop() {
        SessionFlags.setActive(false)
        SessionFlags.setActive(false)
        XCTAssertFalse(SessionFlags.isActive(), "Double-stop must leave session inactive")
    }

    func testWatchdogTaskIdentifierMatchesInfoPlist() {
        // The BGTaskSchedulerPermittedIdentifiers key in Info.plist must match exactly.
        XCTAssertEqual("org.akshrava.ios.watchdog", Watchdog.taskIdentifier,
            "Task identifier must match Info.plist BGTaskSchedulerPermittedIdentifiers")
    }
}
