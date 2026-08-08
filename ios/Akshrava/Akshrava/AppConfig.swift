//
//  AppConfig.swift
//  Akshrava iOS
//
//  Configuration system supporting iOS 14.0+ through iOS 20.0+.
//  Enforces strict assistive vision boundaries (awareness only).
//

import Foundation

public struct AppConfig {
    public static let shared = AppConfig()
    
    public let appVersion: String = "0.2.13"
    public let buildCode: Int = 13
    public let minSupportedOSVersion: String = "14.0"
    
    // Default timeouts & thresholds matching backend protocol specs
    public let alertMaxAgeMs: Int = 2500
    public let maxFrameJpegSizeBytes: Int = 500 * 1024 // 500 KB
    public let minFrameIntervalMs: Int64 = 200
    public let defaultStationaryIntervalMs: Int64 = 5000
    
    public var wssEndpointURL: URL {
        if let envUrl = ProcessInfo.processInfo.environment["AKSHRAVA_WSS_URL"], let url = URL(string: envUrl) {
            return url
        }
        if let stored = UserDefaults.standard.string(forKey: "akshrava_wss_url"), let url = URL(string: stored) {
            return url
        }
        return URL(string: "wss://api.akshrava.org/v1/session")!
    }
    
    private init() {}
}
