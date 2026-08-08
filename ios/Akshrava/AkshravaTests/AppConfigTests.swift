//
//  AppConfigTests.swift
//  AkshravaTests
//
//  Mirrors AppConfigTest.kt — validates AppConfig defaults and WSS URL resolution.
//

import XCTest
@testable import Akshrava

final class AppConfigTests: XCTestCase {

    func testAppVersionMatchesExpected() {
        XCTAssertEqual(AppConfig.shared.appVersion, "0.2.13")
    }

    func testBuildCodeMatchesExpected() {
        XCTAssertEqual(AppConfig.shared.buildCode, 13)
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

    func testMinFrameIntervalIsAtLeast200ms() {
        XCTAssertGreaterThanOrEqual(AppConfig.shared.minFrameIntervalMs, 200)
    }

    func testStationaryIntervalIs5Seconds() {
        XCTAssertEqual(AppConfig.shared.defaultStationaryIntervalMs, 5000)
    }
}
