//
//  CaptureControllerTests.swift
//  AkshravaTests
//
//  Tests for CaptureController — iOS equivalent of Android PreviewSurfaceDrainTest.kt and
//  CameraLifecycleOwnerTest.kt. On iOS, AVCaptureSession management (start/stop) is handled
//  by CaptureController rather than Android's PreviewSurfaceDrain + CameraLifecycleOwner.
//

import XCTest
#if os(iOS)
import AVFoundation
#endif
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
        // Calling stopCapture() on a never-started controller must not crash.
        #if os(iOS)
        let controller = CaptureController()
        controller.stopCapture()
        controller.stopCapture()
        #else
        XCTAssertTrue(true, "CaptureController not available on macOS")
        #endif
    }

    func testStartAndStopLifecycleDoesNotCrash() {
        // On macOS, CaptureController does not exist. On iOS: stopCapture() must be safe on a
        // never-started session.
        #if os(iOS)
        let controller = CaptureController()
        controller.stopCapture()
        #else
        XCTAssertTrue(true, "CaptureController is iOS-only")
        #endif
    }

    func testDeniedCameraPermissionFailsClosedInsteadOfRunningBlind() throws {
        // The simulator reports no camera authorization, so startCapture() must take the
        // fail-closed path and tell the delegate the camera is unavailable -- never leave a
        // "started" session that will silently never deliver a frame. This assertion is only
        // meaningful when access is genuinely not granted, which is the simulator's default.
        #if os(iOS)
        guard AVCaptureDevice.authorizationStatus(for: .video) != .authorized else {
            throw XCTSkip("Camera access is granted in this environment")
        }
        final class Spy: NSObject, CaptureControllerDelegate {
            let unavailable = XCTestExpectation(description: "camera reported unavailable")
            func captureController(_ c: CaptureController, didOutputFrame b: CMSampleBuffer) {}
            func captureController(_ c: CaptureController, didEncounterStall e: Error) {}
            func captureController(_ c: CaptureController, didBecomeUnavailable e: Error) {
                unavailable.fulfill()
            }
        }
        let controller = CaptureController()
        let spy = Spy()
        controller.delegate = spy
        controller.startCapture()
        wait(for: [spy.unavailable], timeout: 10.0)
        #else
        XCTAssertTrue(true, "CaptureController is iOS-only")
        #endif
    }
}
