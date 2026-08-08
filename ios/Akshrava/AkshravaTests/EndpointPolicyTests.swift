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
        XCTAssertTrue(url.scheme == "wss" || url.scheme == "ws")
    }

    func testInvalidUrlFallsBackToDefault() {
        let url = EndpointPolicy.resolveEndpoint(customURLString: "not_a_valid_url")
        // Should fall back to default (wss:// or ws://)
        XCTAssertTrue(url.scheme == "wss" || url.scheme == "ws")
    }

    func testHttpUrlIsRejectedAndFallsBack() {
        // Only wss:// and ws:// are valid — http:// must not be accepted
        let url = EndpointPolicy.resolveEndpoint(customURLString: "http://insecure.example.com")
        XCTAssertTrue(url.scheme == "wss" || url.scheme == "ws",
                      "HTTP endpoint must be rejected; got: \(url)")
    }
}
