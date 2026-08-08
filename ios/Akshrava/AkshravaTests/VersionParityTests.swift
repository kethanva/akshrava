//
//  VersionParityTests.swift
//  AkshravaTests
//
//  Mirrors check_release_version.py in test form.
//  Ensures iOS version is always in sync with the declared contract.
//

import XCTest
@testable import Akshrava

final class VersionParityTests: XCTestCase {

    func testVersionIs0_2_14() {
        // This test pins the current release. When bumping to 0.2.15,
        // all three files (AppConfig.swift, build.gradle.kts, pyproject.toml) must be bumped together.
        XCTAssertEqual(AppConfig.shared.appVersion, "0.2.14")
    }

    func testBuildCodeIs14() {
        XCTAssertEqual(AppConfig.shared.buildCode, 14)
    }

    func testVersionStringMatchesBuildCodeSuffix() {
        // versionCode should equal the patch component of versionName
        let parts = AppConfig.shared.appVersion.split(separator: ".")
        XCTAssertEqual(parts.count, 3, "Version must be X.Y.Z")
        if let patchStr = parts.last, let patch = Int(patchStr) {
            XCTAssertEqual(patch, AppConfig.shared.buildCode,
                           "Build code should match patch component of version string")
        }
    }

    func testMinSupportedOSVersionIsConsistentWithiOSTarget() {
        // Package.swift sets .iOS(.v14) — must match Info.plist MinimumOSVersion
        let minVersion = Double(AppConfig.shared.minSupportedOSVersion) ?? 0.0
        XCTAssertGreaterThanOrEqual(minVersion, 14.0)
    }
}
