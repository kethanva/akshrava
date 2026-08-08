//
//  MainViewControllerTests.swift
//  AkshravaTests
//
//  Unit tests for MainViewController (iOS equivalent of Android MainActivityTest.kt).
//

import XCTest
@testable import Akshrava

final class MainViewControllerTests: XCTestCase {

    func testMainViewControllerIsDefinedOnIOS() {
        // Verify the type exists in the module (iOS-only, compiled under #if os(iOS))
        #if os(iOS)
        // On iOS: instantiate via NSClassFromString to avoid linking UIKit in test target
        let cls = NSClassFromString("Akshrava.MainViewController")
        XCTAssertNotNil(cls, "MainViewController must exist in the Akshrava module on iOS")
        #else
        // macOS: class is guarded; test verifies no compile-time error
        XCTAssertTrue(true, "MainViewController not present on macOS (iOS-only)")
        #endif
    }
}
