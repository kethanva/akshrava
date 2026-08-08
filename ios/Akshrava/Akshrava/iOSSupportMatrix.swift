//
//  iOSSupportMatrix.swift
//  Akshrava iOS
//
//  Device support matrix for the iOS client.
//  Guards the minimum supported OS (iOS 14.0) and exposes device identifiers
//  for telemetry / provisioning — mirrors AndroidSupportMatrix.kt.
//

import Foundation

public struct iOSSupportMatrix {
    public static func isSupported() -> Bool {
        #if os(iOS)
        if #available(iOS 14.0, *) {
            return true
        }
        return false
        #else
        // macOS CI / test runner — treat as supported for SPM swift test
        return true
        #endif
    }

    public static func osVersionString() -> String {
        #if os(iOS)
        return ProcessInfo.processInfo.operatingSystemVersionString
        #else
        return ProcessInfo.processInfo.operatingSystemVersionString
        #endif
    }

    public static func deviceModel() -> String {
        var systemInfo = utsname()
        uname(&systemInfo)
        let machineMirror = Mirror(reflecting: systemInfo.machine)
        let identifier = machineMirror.children.reduce("") { identifier, element in
            guard let value = element.value as? Int8, value != 0 else { return identifier }
            return identifier + String(UnicodeScalar(UInt8(value)))
        }
        return identifier.isEmpty ? "simulator" : identifier
    }
}
