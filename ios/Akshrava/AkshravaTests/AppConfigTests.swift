//
//  AppConfigTests.swift
//  AkshravaTests
//
//  Mirrors AppConfigTest.kt — validates AppConfig defaults and WSS URL resolution.
//

import XCTest
@testable import Akshrava

final class AppConfigTests: XCTestCase {

    func testAppVersionIsSet() {
        XCTAssertEqual(AppConfig.shared.appVersion, "0.2.14")
    }

    func testBuildCodeIsSet() {
        XCTAssertEqual(AppConfig.shared.buildCode, 14)
    }

    func testMinSupportedOSVersionIsIOS14() {
        XCTAssertEqual(AppConfig.shared.minSupportedOSVersion, "14.0")
    }

    func testAlertMaxAgeMsIsConsistentWithBackend() {
        // Backend contract: alert_max_age_ms = 2500 (enforced by ProtocolClient.STALE_ALERT_MS)
        XCTAssertEqual(AppConfig.shared.alertMaxAgeMs, 2500)
    }

    func testMaxFrameJpegSizeBytesIsWithinProtocolLimit() {
        // Backend rejects frames > 500 KB
        XCTAssertEqual(AppConfig.shared.maxFrameJpegSizeBytes, 500 * 1024)
        XCTAssertLessThanOrEqual(AppConfig.shared.maxFrameJpegSizeBytes, 500 * 1024)
    }

    func testWssEndpointFallsBackToDefault() {
        // Without env override, resolves to a wss:// URL
        let url = AppConfig.shared.wssEndpointURL
        XCTAssertTrue(url.scheme == "wss" || url.scheme == "ws",
                      "Endpoint URL must use WSS or WS scheme, got: \(url)")
    }

    func testUnconfiguredDefaultEndpointIsAnUnresolvableHost() throws {
        // H11: the unconfigured fallback must be a host that can never exist (RFC 2606 `.invalid`
        // TLD), not a real-looking domain a misconfigured build could silently start streaming
        // camera frames and a bearer JWT to. Only meaningful absent AKSHRAVA_WSS_URL / stored
        // UserDefaults override, which is the default state of this test process.
        guard ProcessInfo.processInfo.environment["AKSHRAVA_WSS_URL"] == nil else {
            throw XCTSkip("AKSHRAVA_WSS_URL is set in this environment")
        }
        let url = AppConfig.shared.wssEndpointURL
        XCTAssertEqual(url.host?.hasSuffix(".invalid"), true)
    }

    func testMinFrameIntervalIsAtLeast200ms() {
        XCTAssertGreaterThanOrEqual(AppConfig.shared.minFrameIntervalMs, 200)
    }

    func testStationaryIntervalIs5Seconds() {
        XCTAssertEqual(AppConfig.shared.defaultStationaryIntervalMs, 5000)
    }
}
