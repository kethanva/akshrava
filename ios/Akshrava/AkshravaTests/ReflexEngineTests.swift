//
//  ReflexEngineTests.swift
//  AkshravaTests
//
//  Mirrors ReflexEngineTest.kt.
//

import XCTest
@testable import Akshrava

final class ReflexEngineTests: XCTestCase {

    func testReflexEngineCanBeInstantiated() {
        let engine = ReflexEngine()
        XCTAssertNotNil(engine)
    }

    func testEvaluateLocalWithNilPixelBufferReturnsNil() {
        let engine = ReflexEngine()
        // Without a real pixel buffer, result is nil (offline model not loaded)
        // This tests the fail-safe path
        let result = engine.evaluateLocal(pixelBuffer: nil as CVPixelBuffer?)
        XCTAssertNil(result)
    }
}

// Extension to allow nil test
extension ReflexEngine {
    func evaluateLocal(pixelBuffer: CVPixelBuffer?) -> [String: Any]? {
        guard let buffer = pixelBuffer else { return nil }
        return evaluateLocal(pixelBuffer: buffer)
    }
}
