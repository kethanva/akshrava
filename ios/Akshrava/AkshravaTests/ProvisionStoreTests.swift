import XCTest
@testable import Akshrava

final class ProvisionStoreTests: XCTestCase {
    func testDeviceProvisionIsReadyRequiresTokenAndCalibration() {
        var provision = DeviceProvision(endpoint: "wss://example.test/v1/session", deviceToken: "", calibrationId: "c1")
        XCTAssertFalse(provision.isReady)
        provision.deviceToken = "tok"
        provision.calibrationId = "unprovisioned"
        XCTAssertFalse(provision.isReady)
        provision.calibrationId = "phone-1"
        XCTAssertTrue(provision.isReady)
    }

    func testLoadReturnsEndpointFallback() {
        let loaded = ProvisionStore.load()
        XCTAssertFalse(loaded.endpoint.isEmpty)
    }
}
