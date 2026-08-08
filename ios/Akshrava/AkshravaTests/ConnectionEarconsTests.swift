//
//  ConnectionEarconsTests.swift
//  AkshravaTests
//
//  Mirrors ConnectionEarconsTest.kt.
//

import XCTest
@testable import Akshrava

final class ConnectionEarconsTests: XCTestCase {

    func testPlayOpenDoesNotCrash() {
        // AudioServicesPlaySystemSound is a no-op in test environment but must not throw
        XCTAssertNoThrow(ConnectionEarcons.playOpen())
    }

    func testPlayDroppedDoesNotCrash() {
        XCTAssertNoThrow(ConnectionEarcons.playDropped())
    }

    func testPlayRestoredDoesNotCrash() {
        XCTAssertNoThrow(ConnectionEarcons.playRestored())
    }
}
