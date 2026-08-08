// swift-tools-version:5.9
//
// Package.swift — Akshrava iOS
//
// Swift Package Manager build manifest.
// Minimum deployment: iOS 14.0 (covers the donated-phone cohort).
// Mirrors the Android Gradle structure: one `Akshrava` library target + one `AkshravaTests` test target.
//
// swift test runs on macOS CI using the macOS SDK; all iOS-framework–dependent code
// is guarded with `#if os(iOS)` so the logic-only tests compile cleanly on macOS.
//

import PackageDescription

let package = Package(
    name: "Akshrava",
    platforms: [
        .iOS(.v14),
        .macOS(.v12), // For `swift test` on macOS CI
    ],
    products: [
        .library(
            name: "Akshrava",
            targets: ["Akshrava"]
        ),
    ],
    targets: [
        .target(
            name: "Akshrava",
            path: "Akshrava",
            exclude: ["Info.plist"],
            swiftSettings: [
                // Propagate debug-build flag like Android's DEBUG BuildConfig
                .define("AKSHRAVA_DEBUG", .when(configuration: .debug)),
            ]
        ),
        .testTarget(
            name: "AkshravaTests",
            dependencies: ["Akshrava"],
            path: "AkshravaTests"
        ),
    ]
)
