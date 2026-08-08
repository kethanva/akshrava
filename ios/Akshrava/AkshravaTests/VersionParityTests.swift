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

    func testVersionIsPinnedToReleaseContract() {
        // Pin must match backend pyproject + Android versionName. Bump all together.
        XCTAssertEqual(AppConfig.shared.appVersion, "0.2.13")
    }

    func testBuildCodeMatchesPatchComponent() {
        XCTAssertEqual(AppConfig.shared.buildCode, 13)
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
