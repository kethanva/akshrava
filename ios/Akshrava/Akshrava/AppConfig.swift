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
    
    public let appVersion: String = "0.2.14"
    public let buildCode: Int = 14
    public let minSupportedOSVersion: String = "14.0"
    
    // Default timeouts & thresholds matching backend protocol specs
    public let alertMaxAgeMs: Int = 2500
    public let maxFrameJpegSizeBytes: Int = 500 * 1024 // 500 KB
    public let minFrameIntervalMs: Int64 = 200
    public let defaultStationaryIntervalMs: Int64 = 5000
    
    public var wssEndpointURL: URL {
        if let envUrl = ProcessInfo.processInfo.environment["AKSHRAVA_WSS_URL"],
           let url = Self.validWssURL(envUrl) {
            return url
        }
        if let stored = UserDefaults.standard.string(forKey: "akshrava_wss_url"),
           let url = Self.validWssURL(stored) {
            return url
        }
        // `.invalid` is an IANA-reserved TLD (RFC 2606) guaranteed to never resolve. A build that
        // ships this placeholder must fail loudly by refusing to connect anywhere, mirroring the
        // Android rule: a build shipping its placeholder can never silently reach a real backend
        // (or, worse here, a plausible-looking third-party domain this project does not control)
        // — set AKSHRAVA_WSS_URL or provision the endpoint before building for a real phone.
        return URL(string: "wss://placeholder.invalid/v1/session")!
    }

    /// Configuration can arrive from process environment, persisted provisioning, or a future
    /// UI. Validate every source here rather than relying on ATS to reject plaintext after the
    /// camera and WebSocket setup have already started.
    public static func validWssURL(_ value: String) -> URL? {
        guard let url = URL(string: value),
              url.scheme?.lowercased() == "wss",
              let host = url.host,
              !host.isEmpty else {
            return nil
        }
        return url
    }
    
    private init() {}
}
