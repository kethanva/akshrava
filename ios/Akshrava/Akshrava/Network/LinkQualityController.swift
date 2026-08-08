//
//  LinkQualityController.swift
//  Akshrava iOS
//

import Foundation

public enum ResolutionScale: Equatable {
    case full480p
    case downscale360p
}

public class LinkQualityController {
    public private(set) var currentScale: ResolutionScale = .full480p
    
    public init() {}
    
    public func update(rttMs: Int) {
        if rttMs > 800 {
            currentScale = .downscale360p
        } else {
            currentScale = .full480p
        }
    }
}
