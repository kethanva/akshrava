//
//  DeviceCapability.swift
//  Akshrava iOS
//
//  iOS device capability detection — mirrors Android DeviceCapability.kt.
//  Provides battery status text (spoken F-15 battery gauge) and simulated-environment
//  detection. iOS device memory / RAM-class checks use ProcessInfo.
//

import Foundation
#if os(iOS)
import UIKit
#endif

public enum DeviceCapability {
    /// Percent-per-hour a live assistance session costs on typical donated hardware.
    /// Coarse estimate: capture rate, screen-on, radio, and battery health all move it.
    public static let sessionDrainPercentPerHour: Double = 4.0

    /// True when running on a simulator (not a real device).
    public static func isSimulator() -> Bool {
        #if targetEnvironment(simulator)
        return true
        #else
        return false
        #endif
    }

    /// True when physical RAM is low (< 2.8 GB total) — mirrors Android low-RAM policy.
    public static func isConstrained() -> Bool {
        let physicalMemory = ProcessInfo.processInfo.physicalMemory
        let threshold: UInt64 = UInt64(2.8 * 1024 * 1024 * 1024)
        return physicalMemory < threshold
    }

    /// Current battery percent (0–100), or nil when monitoring is unavailable (macOS CI / unknown).
    public static func batteryPercent() -> Int? {
        #if os(iOS)
        let device = UIDevice.current
        if !device.isBatteryMonitoringEnabled {
            device.isBatteryMonitoringEnabled = true
        }
        let level = device.batteryLevel
        guard level >= 0 else { return nil }
        return Int((level * 100).rounded())
        #else
        return nil
        #endif
    }

    /// Spoken F-15 battery gauge text.
    /// The `pct` parameter accepts an optional integer (0–100) matching the Android API.
    /// Returns unavailable text when nil — phrased as an estimate, never "0 hours".
    public static func batteryStatusText(pct: Int?) -> String {
        guard let pct = pct else { return "Battery level unavailable." }
        let hours = Double(pct) / sessionDrainPercentPerHour
        let estimate: String
        switch hours {
        case 1.5...:     estimate = "roughly \(Int(hours.rounded())) hours"
        case 0.75..<1.5: estimate = "roughly one hour"
        default:         estimate = "less than an hour"
        }
        return "Battery at \(pct) percent. Estimated \(estimate) of assistance time remaining."
    }
}
