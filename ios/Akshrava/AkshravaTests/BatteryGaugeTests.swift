//
//  BatteryGaugeTests.swift
//  AkshravaTests
//
//  F-15 auditory battery gauge wording tests — mirrors Android BatteryGaugeTest.kt.
//

import XCTest
@testable import Akshrava

final class BatteryGaugeTests: XCTestCase {

    func testReportsPercentAndAnEstimate() {
        let text = DeviceCapability.batteryStatusText(pct: 80)
        XCTAssertTrue(text.contains("80 percent"), "Text must contain percentage: \(text)")
        XCTAssertTrue(text.contains("roughly 20 hours"), "80% at 4%/hr = 20 hours: \(text)")
    }

    func testNeverClaimsZeroHoursWhilePhoneIsStillRunning() {
        let zeroPattern = #"\b0 hours\b"#
        let regex = try! NSRegularExpression(pattern: zeroPattern)
        for pct in 1...100 {
            let text = DeviceCapability.batteryStatusText(pct: pct)
            let range = NSRange(text.startIndex..., in: text)
            let matches = regex.numberOfMatches(in: text, range: range)
            XCTAssertEqual(0, matches, "pct=\(pct) must not say '0 hours': \(text)")
        }
    }

    func testLowBatteryDegradesToACoarsePhrase() {
        XCTAssertTrue(DeviceCapability.batteryStatusText(pct: 2).contains("less than an hour"),
                      "2% must say 'less than an hour'")
        XCTAssertTrue(DeviceCapability.batteryStatusText(pct: 3).contains("roughly one hour"),
                      "3% must say 'roughly one hour'")
    }

    func testUnknownLevelSaysSoRatherThanGuessing() {
        XCTAssertEqual("Battery level unavailable.", DeviceCapability.batteryStatusText(pct: nil))
    }

    func testEstimateIsAlwaysMarkedAsAnEstimate() {
        // The drain figure is a coarse constant; wording must not imply a measurement.
        for pct in [1, 15, 50, 100] {
            let text = DeviceCapability.batteryStatusText(pct: pct)
            XCTAssertTrue(text.contains("Estimated"),
                          "pct=\(pct) must contain 'Estimated': \(text)")
        }
    }
}
