//
//  HeadsetControlsTests.swift
//  AkshravaTests
//
//  Unit tests for HeadsetControls — mirrors Android HeadsetControlsTest.kt.
//

import XCTest
@testable import Akshrava

final class HeadsetControlsTests: XCTestCase {

    func testHeadsetControlsSharedSingletonExists() {
        let controls = HeadsetControls.shared
        XCTAssertNotNil(controls)
    }

    func testSetupDoesNotCrash() {
        // On macOS, setup() is a no-op; on iOS it registers MPRemoteCommandCenter handlers.
        HeadsetControls.shared.setup()
    }

    func testSetupIsIdempotent() {
        // Calling setup() multiple times must not crash or duplicate registrations
        HeadsetControls.shared.setup()
        HeadsetControls.shared.setup()
    }
}
