//
//  CapturePolicy.swift
//  Akshrava iOS
//

import Foundation

public class CapturePolicy {
    private var lastCaptureMonoMs: Int64 = 0
    
    public init() {}
    
    public func shouldCapture(isMoving: Bool, currentMonoMs: Int64) -> Bool {
        let minInterval: Int64 = isMoving ? 500 : 5000 // 0.2 FPS stationary, 2 FPS moving
        if currentMonoMs - lastCaptureMonoMs >= minInterval {
            lastCaptureMonoMs = currentMonoMs
            return true
        }
        return false
    }
    
    public func reset() {
        lastCaptureMonoMs = 0
    }
}
