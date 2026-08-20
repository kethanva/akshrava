//
//  AgentDebugLogTests.swift
//  AkshravaTests
//
//  Mirrors AgentDebugLogTest.kt.
//

import XCTest
@testable import Akshrava

final class AgentDebugLogTests: XCTestCase {

    func testLogDoesNotCrash() {
        XCTAssertNoThrow(AgentDebugLog.log(message: "test message"))
    }

    func testLogAcceptsEmptyMessage() {
        XCTAssertNoThrow(AgentDebugLog.log(message: ""))
    }

    func testLogAcceptsUnicodeMessage() {
        XCTAssertNoThrow(AgentDebugLog.log(message: "वाहन पास है 車 🚗"))
    }

    func testOperationalErrorLogDoesNotCrash() {
        XCTAssertNoThrow(AgentDebugLog.error(event: "test_failure", detail: "private detail"))
    }
}
