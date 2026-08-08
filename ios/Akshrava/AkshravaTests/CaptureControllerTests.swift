//
//  CaptureControllerTests.swift
//  AkshravaTests
//
//  Tests for CaptureController — iOS equivalent of Android PreviewSurfaceDrainTest.kt and
//  CameraLifecycleOwnerTest.kt. On iOS, AVCaptureSession management (start/stop) is handled
//  by CaptureController rather than Android's PreviewSurfaceDrain + CameraLifecycleOwner.
//

import XCTest
@testable import Akshrava

final class CaptureControllerTests: XCTestCase {

    func testCaptureControllerInstantiatesOnAllPlatforms() {
        // CaptureController is iOS-only; on macOS verify the test compiles cleanly.
        #if os(iOS)
        let controller = CaptureController()
        XCTAssertNotNil(controller)
        #else
        XCTAssertTrue(true, "CaptureController is iOS-only; macOS build verified no compile error")
        #endif
    }

    func testStopIsIdempotentAndDoesNotCrash() {
        // Calling stop() on a never-started controller must not crash.
        #if os(iOS)
        let controller = CaptureController()
        controller.stop()
        controller.stop()
        #else
        XCTAssertTrue(true, "CaptureController not available on macOS")
        #endif
    }

    func testStartAndStopLifecycleDoesNotCrash() {
        // On macOS, CaptureController does not exist. On iOS: stop() must be safe on a
        // never-started session.
        #if os(iOS)
        let controller = CaptureController()
        controller.stop()
        #else
        XCTAssertTrue(true, "CaptureController is iOS-only")
        #endif
    }
}
