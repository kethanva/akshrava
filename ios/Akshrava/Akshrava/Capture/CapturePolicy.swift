//
//  CapturePolicy.swift
//  Akshrava iOS
//

import Foundation

public class CapturePolicy {
    /// `nil` means no capture has happened yet (first frame always passes).
    private var lastCaptureMonoMs: Int64?

    public init() {}

    public func shouldCapture(isMoving: Bool, currentMonoMs: Int64) -> Bool {
        let minInterval: Int64 = isMoving ? 500 : 5000 // 0.2 FPS stationary, 2 FPS moving
        if let last = lastCaptureMonoMs, currentMonoMs - last < minInterval {
            return false
        }
        lastCaptureMonoMs = currentMonoMs
        return true
    }

    public func reset() {
        lastCaptureMonoMs = nil
    }
}
