//
//  AssistSessionManagerTests.swift
//  AkshravaTests
//
//  Mirrors AssistServiceTest.kt.
//

import XCTest
@testable import Akshrava

final class AssistSessionManagerTests: XCTestCase {

    func testAssistSessionManagerSharedIsNonNil() {
        XCTAssertNotNil(AssistSessionManager.shared)
    }

    func testStopSessionWhenNotRunningDoesNotCrash() {
        XCTAssertNoThrow(AssistSessionManager.shared.stopSession())
    }

    func testDoubleStopDoesNotCrash() {
        AssistSessionManager.shared.stopSession()
        XCTAssertNoThrow(AssistSessionManager.shared.stopSession())
    }
}
