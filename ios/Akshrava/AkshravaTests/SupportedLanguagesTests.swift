//
//  SupportedLanguagesTests.swift
//  AkshravaTests
//
//  Mirrors SupportedLanguagesTest.kt.
//

import XCTest
@testable import Akshrava

final class SupportedLanguagesTests: XCTestCase {

    func testSixLanguagesAreSupported() {
        XCTAssertEqual(SupportedLanguages.all.count, 6)
    }

    func testEnglishWireCode() {
        XCTAssertEqual(SupportedLanguages.wireCode(for: "en-IN"), "en")
        XCTAssertEqual(SupportedLanguages.wireCode(for: "en"), "en")
    }

    func testHindiWireCode() {
        XCTAssertEqual(SupportedLanguages.wireCode(for: "hi-IN"), "hi")
        XCTAssertEqual(SupportedLanguages.wireCode(for: "hi"), "hi")
    }

    func testTamilWireCode() {
        XCTAssertEqual(SupportedLanguages.wireCode(for: "ta-IN"), "ta")
    }

    func testKannadaWireCode() {
        XCTAssertEqual(SupportedLanguages.wireCode(for: "kn-IN"), "kn")
    }

    func testMalayalamWireCode() {
        XCTAssertEqual(SupportedLanguages.wireCode(for: "ml-IN"), "ml")
    }

    func testTeluguWireCode() {
        XCTAssertEqual(SupportedLanguages.wireCode(for: "te-IN"), "te")
    }

    func testUnknownLocaleDefaultsToEnglish() {
        XCTAssertEqual(SupportedLanguages.wireCode(for: "xx-XX"), "en")
        XCTAssertEqual(SupportedLanguages.wireCode(for: "unknown"), "en")
        XCTAssertEqual(SupportedLanguages.wireCode(for: ""), "en")
    }

    func testWireCodesAreUnique() {
        let wireCodes = SupportedLanguages.all.map { $0.wireCode }
        let unique = Set(wireCodes)
        XCTAssertEqual(wireCodes.count, unique.count, "Duplicate wire codes detected")
    }

    func testTagsAreUnique() {
        let tags = SupportedLanguages.all.map { $0.tag }
        let unique = Set(tags)
        XCTAssertEqual(tags.count, unique.count, "Duplicate language tags detected")
    }
}
