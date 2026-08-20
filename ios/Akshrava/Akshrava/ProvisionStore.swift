//
//  ProvisionStore.swift
//  Akshrava iOS
//
//  Durable endpoint / JWT / calibration storage. Token lives in Keychain (not UserDefaults).
//

import Foundation
import Security

public struct DeviceProvision {
    public var endpoint: String
    public var deviceToken: String
    public var language: String
    public var calibrationId: String

    public init(
        endpoint: String = "",
        deviceToken: String = "",
        language: String = "en",
        calibrationId: String = "unprovisioned"
    ) {
        self.endpoint = endpoint
        self.deviceToken = deviceToken
        self.language = language
        self.calibrationId = calibrationId
    }

    public var isReady: Bool {
        let endpointValue = endpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        let calibration = calibrationId.trimmingCharacters(in: .whitespacesAndNewlines)
        let languageValue = language.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let languageIsSupported = SupportedLanguages.all.contains {
            languageValue == $0.tag.lowercased() || languageValue == $0.wireCode
        }
        guard let endpointURL = AppConfig.validWssURL(endpointValue) else { return false }
        return !deviceToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !calibration.isEmpty &&
            calibration != "unprovisioned" &&
            calibration.count <= 128 &&
            languageIsSupported &&
            endpointURL.host?.hasSuffix(".invalid") != true
    }
}

public enum ProvisionStore {
    private static let endpointKey = "endpoint"
    private static let languageKey = "language"
    private static let calibrationKey = "calibration"
    private static let tokenService = "org.akshrava.ios.device-token"
    private static let tokenAccount = "device"

    public static func load() -> DeviceProvision {
        let defaults = UserDefaults.standard
        let endpoint = defaults.string(forKey: endpointKey)
            ?? ProcessInfo.processInfo.environment["AKSHRAVA_WSS_URL"]
            ?? AppConfig.shared.wssEndpointURL.absoluteString
        let language = defaults.string(forKey: languageKey) ?? "en"
        let calibration = defaults.string(forKey: calibrationKey) ?? "unprovisioned"
        let token = loadToken()
            ?? ProcessInfo.processInfo.environment["AKSHRAVA_DEVICE_TOKEN"]
            ?? ""
        return DeviceProvision(
            endpoint: endpoint,
            deviceToken: token,
            language: language,
            calibrationId: calibration
        )
    }

    @discardableResult
    public static func save(_ provision: DeviceProvision) -> Bool {
        let token = provision.deviceToken.trimmingCharacters(in: .whitespacesAndNewlines)
        // Update the Keychain first. UserDefaults writes cannot report a recoverable error, while
        // a Keychain write can; doing them in the opposite order left a partially updated endpoint
        // and calibration behind when secure token persistence failed.
        guard saveToken(token) else {
            AgentDebugLog.error(event: "provision_token_save_failed")
            return false
        }
        let defaults = UserDefaults.standard
        defaults.set(provision.endpoint.trimmingCharacters(in: .whitespacesAndNewlines), forKey: endpointKey)
        defaults.set(provision.language.trimmingCharacters(in: .whitespacesAndNewlines), forKey: languageKey)
        defaults.set(provision.calibrationId.trimmingCharacters(in: .whitespacesAndNewlines), forKey: calibrationKey)
        return true
    }

    /// Debug-only convenience: loads a bundled `provision.json` for local development builds and
    /// writes it into the Keychain/UserDefaults store. Gated to DEBUG builds specifically --
    /// `project.yml` excluding `provision.json` from sources was the only thing preventing this
    /// from shipping in a release IPA with a static device JWT baked in, and from silently
    /// overriding a field-provisioned token on every session start in any build configuration
    /// that happened to include the file. A compiler gate cannot be dropped by an unrelated
    /// xcodegen spec edit the way a source-exclude list can.
    public static func loadFromBundleProvisionJSON() -> DeviceProvision? {
        // AKSHRAVA_DEBUG (not the bare DEBUG flag) is the compilation condition this package
        // actually defines -- see Package.swift's swiftSettings -- and is guaranteed to reflect
        // the app scheme's Debug/Release configuration; a bare `DEBUG` is not defined anywhere in
        // this SPM target and would silently never gate anything.
        #if os(iOS) && AKSHRAVA_DEBUG
        guard let url = Bundle.main.url(forResource: "provision", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        let endpoint = (json["endpoint"] as? String) ?? (json["wss_url"] as? String) ?? ""
        let token = (json["token"] as? String) ?? (json["device_token"] as? String) ?? ""
        let language = (json["language"] as? String) ?? "en"
        let calibration = (json["calibration_id"] as? String) ?? (json["camera_calibration_id"] as? String) ?? "unprovisioned"
        let provision = DeviceProvision(
            endpoint: endpoint,
            deviceToken: token,
            language: language,
            calibrationId: calibration
        )
        // Even a debug bundle must not become an in-memory plaintext fallback when Keychain is
        // unavailable. A failed secure write leaves provisioning incomplete and visible.
        guard save(provision) else { return nil }
        return provision
        #else
        return nil
        #endif
    }

    private static func loadToken() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: tokenService,
            kSecAttrAccount as String: tokenAccount,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            AgentDebugLog.error(
                event: "provision_token_load_failed",
                detail: "keychain_status=\(status)"
            )
            return nil
        }
        guard let token = String(data: data, encoding: .utf8) else {
            AgentDebugLog.error(event: "provision_token_load_failed", detail: "invalid_utf8")
            return nil
        }
        return token
    }

    private static func saveToken(_ token: String) -> Bool {
        let identity: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: tokenService,
            kSecAttrAccount as String: tokenAccount,
        ]

        guard !token.isEmpty else {
            let status = SecItemDelete(identity as CFDictionary)
            return status == errSecSuccess || status == errSecItemNotFound
        }

        let attributes: [String: Any] = [
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let updateStatus = SecItemUpdate(identity as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecSuccess { return true }
        guard updateStatus == errSecItemNotFound else { return false }

        // Add only when there was no old item. Delete-then-add used to destroy a valid field
        // credential before knowing whether Keychain could store its replacement.
        var addition = identity
        attributes.forEach { addition[$0.key] = $0.value }
        return SecItemAdd(addition as CFDictionary, nil) == errSecSuccess
    }
}
