//
//  iOSSupportMatrixTests.swift
//  AkshravaTests
//
//  Mirrors AndroidSupportMatrixTest.kt.
//

import XCTest
@testable import Akshrava

final class iOSSupportMatrixTests: XCTestCase {

    func testIsSupportedReturnsTrueOnCurrentSystem() {
        // We are running in the test harness on iOS 14+
        XCTAssertTrue(iOSSupportMatrix.isSupported())
    }

    func testOSVersionStringIsNonEmpty() {
        let version = iOSSupportMatrix.osVersionString()
        XCTAssertFalse(version.isEmpty)
    }

    func testDeviceModelIsNonEmpty() {
        let model = iOSSupportMatrix.deviceModel()
        XCTAssertFalse(model.isEmpty)
    }

    func testOSVersionStringContainsDotSeparator() {
        let version = iOSSupportMatrix.osVersionString()
        // OS versions are always e.g. "14.5", "17.0"
        XCTAssertTrue(version.contains("."), "Expected dot-separated version, got: \(version)")
    }
}
