//
//  GestureDetectorEngineTests.swift
//  AkshravaTests
//
//  Mirrors GestureDetectorEngineTest.kt.
//

import XCTest
@testable import Akshrava

final class GestureDetectorEngineTests: XCTestCase {

    func testGestureDetectorCanBeInstantiated() {
        let engine = GestureDetectorEngine()
        XCTAssertNotNil(engine)
    }

    func testStopDoesNotCrashWhenNeverStarted() {
        let engine = GestureDetectorEngine()
        XCTAssertNoThrow(engine.stop())
    }

    func testDelegateCanBeSetToNil() {
        let engine = GestureDetectorEngine()
        engine.delegate = nil
        XCTAssertNil(engine.delegate)
    }
}
