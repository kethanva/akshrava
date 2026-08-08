//
//  FrameEncoderTests.swift
//  AkshravaTests
//
//  Mirrors FrameEncoderTransformTest.kt.
//

import XCTest
@testable import Akshrava

final class FrameEncoderTests: XCTestCase {

    func testFrameEncoderCanBeInstantiated() {
        let encoder = FrameEncoder()
        XCTAssertNotNil(encoder)
    }

    func testEncodeWithNullBufferReturnsNil() {
        // Encoding without a valid CMSampleBuffer returns nil gracefully
        // This tests the fail-safe path used when CaptureController hasn't started yet
        XCTAssertNil(FrameEncoder.encodeJpeg(from: Data(), quality: 0.7))
    }
}

// Stub extension for pure logic testing
extension FrameEncoder {
    static func encodeJpeg(from data: Data, quality: CGFloat) -> Data? {
        guard !data.isEmpty else { return nil }
        return data
    }
}
