//
//  EndpointPolicy.swift
//  Akshrava iOS
//

import Foundation

public struct EndpointPolicy {
    /// Only `wss://` is accepted for a custom (provisioned) endpoint. Plaintext `ws://` was
    /// previously accepted here too; ATS (`NSAllowsArbitraryLoads = false`) blocks it in practice
    /// today, but this frame header carries a bearer JWT and camera imagery, and that must never
    /// depend on an App Transport Security configuration elsewhere staying exactly as strict as
    /// it is now to be the only thing stopping a cleartext connection.
    public static func resolveEndpoint(customURLString: String? = nil) -> URL {
        if let custom = customURLString, let url = AppConfig.validWssURL(custom) {
            return url
        }
        return AppConfig.shared.wssEndpointURL
    }
}
