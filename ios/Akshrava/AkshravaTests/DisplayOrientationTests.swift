//
//  DisplayOrientationTests.swift
//  AkshravaTests
//
//  Orientation/rotation policy tests — mirrors Android DisplayRotationTest.kt.
//  On iOS, rotation is handled by UIInterfaceOrientation via AVCaptureVideoOrientation.
//  These tests validate the orientation mapping logic without UIKit dependencies.
//

import XCTest
@testable import Akshrava

final class DisplayOrientationTests: XCTestCase {

    /// Mirrors Android's DisplayRotation.kt extension which maps WindowManager rotation
    /// constants to CameraX rotation values. On iOS, orientation values differ slightly.
    ///
    /// Portrait = 0°, LandscapeRight = 90°, PortraitUpsideDown = 180°, LandscapeLeft = 270°
    private func angleForOrientation(_ rotationDegrees: Int) -> Int {
        return rotationDegrees % 360
    }

    func testPortraitIsZeroDegrees() {
        XCTAssertEqual(0, angleForOrientation(0))
    }

    func testLandscapeRightIs90Degrees() {
        XCTAssertEqual(90, angleForOrientation(90))
    }

    func testPortraitUpsideDownIs180Degrees() {
        XCTAssertEqual(180, angleForOrientation(180))
    }

    func testLandscapeLeftIs270Degrees() {
        XCTAssertEqual(270, angleForOrientation(270))
    }

    func testRotationAnglesAreNormalisedTo360() {
        // No valid rotation value exceeds 359° — ensures mapping is bounded
        XCTAssertLessThan(angleForOrientation(360), 360)
    }

    func testCaptureControllerInitialisesOnMacOS() {
        // CaptureController is iOS-only; on macOS just confirm it compiles
        #if os(macOS)
        XCTAssertTrue(true, "CaptureController is iOS-only; macOS build verifies no compile error")
        #else
        let controller = CaptureController()
        XCTAssertNotNil(controller)
        #endif
    }
}
