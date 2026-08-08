//
//  EndpointPolicy.swift
//  Akshrava iOS
//

import Foundation

public struct EndpointPolicy {
    public static func resolveEndpoint(customURLString: String? = nil) -> URL {
        if let custom = customURLString, let url = URL(string: custom), url.scheme == "wss" || url.scheme == "ws" {
            return url
        }
        return AppConfig.shared.wssEndpointURL
    }
}
