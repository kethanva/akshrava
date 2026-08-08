//
//  WatchdogTests.swift
//  AkshravaTests
//
//  Mirrors WatchdogTest.kt.
//

import XCTest
@testable import Akshrava

final class WatchdogTests: XCTestCase {

    func testWatchdogSharedInstanceIsNonNil() {
        XCTAssertNotNil(Watchdog.shared)
    }

    func testRegisterBackgroundTasksDoesNotCrash() {
        // BGTaskScheduler registration is idempotent in test environment
        XCTAssertNoThrow(Watchdog.shared.registerBackgroundTasks())
    }
}
