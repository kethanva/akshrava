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
        !deviceToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            !calibrationId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty &&
            calibrationId != "unprovisioned" &&
            URL(string: endpoint) != nil
    }
}

public enum ProvisionStore {
    private static let defaultsSuite = "org.akshrava.ios.provision"
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
        let defaults = UserDefaults.standard
        defaults.set(provision.endpoint.trimmingCharacters(in: .whitespacesAndNewlines), forKey: endpointKey)
        defaults.set(provision.language, forKey: languageKey)
        defaults.set(provision.calibrationId.trimmingCharacters(in: .whitespacesAndNewlines), forKey: calibrationKey)
        return saveToken(provision.deviceToken.trimmingCharacters(in: .whitespacesAndNewlines))
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
        var provision = DeviceProvision(
            endpoint: endpoint,
            deviceToken: token,
            language: language,
            calibrationId: calibration
        )
        _ = save(provision)
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
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func saveToken(_ token: String) -> Bool {
        SecItemDelete([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: tokenService,
            kSecAttrAccount as String: tokenAccount,
        ] as CFDictionary)

        guard !token.isEmpty else { return true }

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: tokenService,
            kSecAttrAccount as String: tokenAccount,
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }
}
