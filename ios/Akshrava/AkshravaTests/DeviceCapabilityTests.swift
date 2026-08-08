//
//  DeviceCapabilityTests.swift
//  AkshravaTests
//
//  Unit tests for DeviceCapability — mirrors Android DeviceCapabilityTest.kt.
//

import XCTest
@testable import Akshrava

final class DeviceCapabilityTests: XCTestCase {

    func testIsSimulatorExecutesSafely() {
        // Should never crash; returns true in simulator, false on real device
        let _ = DeviceCapability.isSimulator()
    }

    func testIsConstrainedExecutesSafely() {
        // Should not crash regardless of host environment
        let _ = DeviceCapability.isConstrained()
    }

    func testBatteryPercentNilOnMacOS() {
        // On macOS (CI) battery monitoring is unavailable; must return nil, not crash
        #if os(macOS)
        XCTAssertNil(DeviceCapability.batteryPercent(),
                     "batteryPercent() must return nil on macOS")
        #endif
    }

    func testBatteryStatusTextNilPctReturnsUnavailable() {
        let text = DeviceCapability.batteryStatusText(pct: nil)
        XCTAssertEqual("Battery level unavailable.", text)
    }

    func testBatteryStatusTextHighBatteryFormatsHours() {
        let text = DeviceCapability.batteryStatusText(pct: 80)
        XCTAssertTrue(text.contains("80 percent"), "Must state percentage: \(text)")
        // 80 / 4.0 = 20 hours → "roughly 20 hours"
        XCTAssertTrue(text.contains("hours"), "High battery must state hours: \(text)")
    }

    func testBatteryStatusTextLowBatteryFormatsLessThanAnHour() {
        let text = DeviceCapability.batteryStatusText(pct: 2)
        XCTAssertTrue(text.contains("2 percent"), "Must state percentage: \(text)")
        // 2 / 4.0 = 0.5 hours → "less than an hour"
        XCTAssertTrue(text.contains("less than an hour"), "Very low battery must say less than an hour: \(text)")
    }

    func testBatteryStatusTextMidBatteryFormatsRoughlyOneHour() {
        // 12 / 4.0 = 3 hours → "roughly 3 hours"; test 4% → "less than an hour", 12% → "roughly 3 hours"
        // Test boundary: 6 / 4.0 = 1.5 hours → "roughly 2 hours" or "roughly one hour"
        // Specifically: 3 / 4.0 = 0.75 → "roughly one hour"
        let text = DeviceCapability.batteryStatusText(pct: 3)
        XCTAssertTrue(text.contains("3 percent"), "Must state percentage: \(text)")
        XCTAssertTrue(text.contains("roughly one hour"), "0.75 hours must say roughly one hour: \(text)")
    }

    func testSessionDrainRateIsPositive() {
        XCTAssertGreaterThan(DeviceCapability.sessionDrainPercentPerHour, 0.0)
    }

    func testBatteryStatusTextNeverSaysZeroHoursWhileRunning() {
        // A running phone must never tell the user "0 hours" — it must say "less than an hour"
        let text = DeviceCapability.batteryStatusText(pct: 1)
        XCTAssertFalse(text.contains("0 hour"), "Must not say 0 hours: \(text)")
        XCTAssertTrue(text.contains("less than an hour") || text.contains("roughly"),
                      "Minimal battery must still give a non-zero estimate: \(text)")
    }
}
