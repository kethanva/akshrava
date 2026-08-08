//
//  HapticFeedbackEngineTests.swift
//  AkshravaTests
//
//  Mirrors HapticFeedbackEngineTest.kt.
//

import XCTest
@testable import Akshrava

final class HapticFeedbackEngineTests: XCTestCase {

    func testHapticEngineCanBeInstantiated() {
        let engine = HapticFeedbackEngine()
        XCTAssertNotNil(engine)
    }

    func testTriggerUrgentDoesNotCrash() {
        let engine = HapticFeedbackEngine()
        XCTAssertNoThrow(engine.triggerUrgent())
    }

    func testTriggerCautionDoesNotCrash() {
        let engine = HapticFeedbackEngine()
        XCTAssertNoThrow(engine.triggerCaution())
    }
}
