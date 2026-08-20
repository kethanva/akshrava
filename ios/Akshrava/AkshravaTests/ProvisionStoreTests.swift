import XCTest
@testable import Akshrava

final class ProvisionStoreTests: XCTestCase {
    func testDeviceProvisionIsReadyRequiresTokenAndCalibration() {
        var provision = DeviceProvision(endpoint: "wss://example.test/v1/session", deviceToken: "", calibrationId: "c1")
        XCTAssertFalse(provision.isReady)
        provision.deviceToken = "tok"
        provision.calibrationId = "unprovisioned"
        XCTAssertFalse(provision.isReady)
        provision.calibrationId = " unprovisioned "
        XCTAssertFalse(provision.isReady)
        provision.calibrationId = "phone-1"
        XCTAssertTrue(provision.isReady)
    }

    func testDeviceProvisionRequiresSecureNonPlaceholderEndpoint() {
        let plaintext = DeviceProvision(
            endpoint: "ws://example.test/v1/session",
            deviceToken: "tok",
            language: "en-IN",
            calibrationId: "phone-1"
        )
        XCTAssertFalse(plaintext.isReady)

        var placeholder = plaintext
        placeholder.endpoint = "wss://placeholder.invalid/v1/session"
        XCTAssertFalse(placeholder.isReady)

        var secure = placeholder
        secure.endpoint = " wss://example.test/v1/session "
        XCTAssertTrue(secure.isReady)
    }

    func testDeviceProvisionRejectsUnknownLanguageAndOversizedCalibration() {
        var provision = DeviceProvision(
            endpoint: "wss://example.test/v1/session",
            deviceToken: "tok",
            language: "unknown",
            calibrationId: "phone-1"
        )
        XCTAssertFalse(provision.isReady)
        provision.language = "hi-IN"
        provision.calibrationId = String(repeating: "c", count: 129)
        XCTAssertFalse(provision.isReady)
        provision.calibrationId = String(repeating: "c", count: 128)
        XCTAssertTrue(provision.isReady)
    }

    func testLoadReturnsEndpointFallback() {
        let loaded = ProvisionStore.load()
        XCTAssertFalse(loaded.endpoint.isEmpty)
    }
}
