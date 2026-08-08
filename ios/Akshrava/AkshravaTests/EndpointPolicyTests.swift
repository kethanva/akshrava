//
//  EndpointPolicyTests.swift
//  AkshravaTests
//
//  Mirrors EndpointPolicyTest.kt.
//

import XCTest
@testable import Akshrava

final class EndpointPolicyTests: XCTestCase {

    func testCustomWssUrlIsUsedWhenProvided() {
        let url = EndpointPolicy.resolveEndpoint(customURLString: "wss://gcp.example.com/v1/session")
        XCTAssertEqual(url.host, "gcp.example.com")
    }

    func testFallsBackToDefaultWhenNilProvided() {
        let url = EndpointPolicy.resolveEndpoint(customURLString: nil)
        XCTAssertNotNil(url)
        XCTAssertEqual(url.scheme, "wss")
    }

    func testInvalidUrlFallsBackToDefault() {
        let url = EndpointPolicy.resolveEndpoint(customURLString: "not_a_valid_url")
        XCTAssertEqual(url.scheme, "wss")
    }

    func testHttpUrlIsRejectedAndFallsBack() {
        // Only wss:// is valid — http:// must not be accepted
        let url = EndpointPolicy.resolveEndpoint(customURLString: "http://insecure.example.com")
        XCTAssertEqual(url.scheme, "wss", "HTTP endpoint must be rejected; got: \(url)")
    }

    func testPlaintextWsUrlIsRejectedAndFallsBack() {
        // H11: plaintext ws:// custom endpoints are no longer accepted. This frame header carries
        // a bearer JWT and camera imagery, and that must not depend solely on an App Transport
        // Security configuration elsewhere staying exactly as strict as it is today.
        let url = EndpointPolicy.resolveEndpoint(customURLString: "ws://insecure.example.com/v1/session")
        XCTAssertNotEqual(url.host, "insecure.example.com")
        XCTAssertEqual(url.scheme, "wss")
    }

    func testHostlessWssUrlIsRejectedAndFallsBack() {
        let url = EndpointPolicy.resolveEndpoint(customURLString: "wss:///v1/session")
        XCTAssertEqual(url.host, "placeholder.invalid")
    }

    func testAppConfigRejectsCleartextAndHostlessUrls() {
        XCTAssertNil(AppConfig.validWssURL("ws://insecure.example.com/v1/session"))
        XCTAssertNil(AppConfig.validWssURL("wss:///v1/session"))
        XCTAssertNotNil(AppConfig.validWssURL("wss://api.example.com/v1/session"))
    }
}
