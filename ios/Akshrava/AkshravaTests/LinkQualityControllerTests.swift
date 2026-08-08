//
//  LinkQualityControllerTests.swift
//  AkshravaTests
//
//  Mirrors LinkQualityControllerTest.kt.
//

import XCTest
@testable import Akshrava

final class LinkQualityControllerTests: XCTestCase {

    func testDefaultScaleIsFull480p() {
        let controller = LinkQualityController()
        XCTAssertEqual(controller.currentScale, .full480p)
    }

    func testHighLatencyDowngradesResolution() {
        let controller = LinkQualityController()
        controller.update(rttMs: 1000) // > 800ms threshold
        XCTAssertEqual(controller.currentScale, .downscale360p)
    }

    func testLowLatencyRestoresFullResolution() {
        let controller = LinkQualityController()
        controller.update(rttMs: 1000)
        controller.update(rttMs: 200)
        XCTAssertEqual(controller.currentScale, .full480p)
    }

    func testBoundaryLatency800msIsHighQuality() {
        let controller = LinkQualityController()
        controller.update(rttMs: 800)
        XCTAssertEqual(controller.currentScale, .full480p)
    }

    func testBoundaryLatency801msIsDowngraded() {
        let controller = LinkQualityController()
        controller.update(rttMs: 801)
        XCTAssertEqual(controller.currentScale, .downscale360p)
    }
}
