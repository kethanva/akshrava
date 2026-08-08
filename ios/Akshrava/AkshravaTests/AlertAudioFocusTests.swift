//
//  AlertAudioFocusTests.swift
//  AkshravaTests
//
//  Unit tests for AlertAudioFocus — mirrors Android AlertAudioFocusTest.kt.
//

import XCTest
@testable import Akshrava

final class AlertAudioFocusTests: XCTestCase {

    func testShouldRequestOnlyWhenReadyAndOpen() {
        XCTAssertTrue(AlertAudioFocus.shouldRequest(ready: true, closed: false))
        XCTAssertFalse(AlertAudioFocus.shouldRequest(ready: false, closed: false))
        XCTAssertFalse(AlertAudioFocus.shouldRequest(ready: true, closed: true))
        XCTAssertFalse(AlertAudioFocus.shouldRequest(ready: false, closed: true))
    }

    func testNestedHoldOnlyAbandonsOnLastRelease() {
        // Mirrors Android AlertManager focusHoldCount: acquire bumps; abandon only when back to zero.
        var holdCount = 0

        XCTAssertTrue(AlertAudioFocus.acquire(holdCount: &holdCount),
                      "First acquisition must return true — requests system focus")
        XCTAssertFalse(AlertAudioFocus.acquire(holdCount: &holdCount),
                       "Nested acquisition (flush/queue) must return false — existing focus retained")
        XCTAssertEqual(2, holdCount)

        XCTAssertFalse(AlertAudioFocus.release(holdCount: &holdCount),
                       "Releasing while another utterance is in progress must not abandon yet")
        XCTAssertTrue(AlertAudioFocus.release(holdCount: &holdCount),
                      "Last utterance done — must return true to signal abandon")
        XCTAssertEqual(0, holdCount)
    }

    func testHoldCountNeverGoesNegative() {
        var holdCount = 0
        _ = AlertAudioFocus.release(holdCount: &holdCount)
        XCTAssertEqual(0, holdCount, "Release with zero hold must not produce negative count")
    }

    func testIsHoldingReflectsAcquireAndRelease() {
        var holdCount = 0
        XCTAssertFalse(AlertAudioFocus.isHolding(holdCount: holdCount))
        _ = AlertAudioFocus.acquire(holdCount: &holdCount)
        XCTAssertTrue(AlertAudioFocus.isHolding(holdCount: holdCount))
        _ = AlertAudioFocus.release(holdCount: &holdCount)
        XCTAssertFalse(AlertAudioFocus.isHolding(holdCount: holdCount))
    }
}
