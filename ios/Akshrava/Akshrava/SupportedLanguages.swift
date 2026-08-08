//
//  SupportedLanguages.swift
//  Akshrava iOS
//
//  Single source of truth for languages supported by the iOS client and wire protocol.
//  Mirrors Android's SupportedLanguages.kt identically.
//

import Foundation

public struct SupportedLanguage {
    public let tag: String       // BCP-47 locale tag e.g. "en-IN"
    public let wireCode: String  // Backend wire code e.g. "en"
    public let label: String     // Human-readable label for UI
}

public enum SupportedLanguages {
    public static let all: [SupportedLanguage] = [
        SupportedLanguage(tag: "en-IN", wireCode: "en", label: "English"),
        SupportedLanguage(tag: "hi-IN", wireCode: "hi", label: "हिन्दी"),
        SupportedLanguage(tag: "ta-IN", wireCode: "ta", label: "தமிழ்"),
        SupportedLanguage(tag: "kn-IN", wireCode: "kn", label: "ಕನ್ನಡ"),
        SupportedLanguage(tag: "ml-IN", wireCode: "ml", label: "മലയാളം"),
        SupportedLanguage(tag: "te-IN", wireCode: "te", label: "తెలుగు"),
    ]

    /// Resolve a BCP-47 locale tag to the wire code the backend expects.
    /// Falls back to "en" for any unknown locale.
    public static func wireCode(for tag: String) -> String {
        let normalized = tag.trimmingCharacters(in: .whitespaces).lowercased()
        return all.first(where: {
            normalized == $0.tag.lowercased() || normalized.hasPrefix($0.wireCode)
        })?.wireCode ?? "en"
    }
}
