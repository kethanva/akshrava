//
//  GestureDetectorEngine.swift
//  Akshrava iOS
//

import Foundation

public protocol GestureDetectorDelegate: AnyObject {
    func gestureDetectorDidDetectManualTrigger(_ engine: GestureDetectorEngine)
}

#if os(iOS)
import CoreMotion

public final class GestureDetectorEngine {
    public static let shakeThresholdG: Double = 2.8
    public static let debounceMs: Int64 = 1_500

    public weak var delegate: GestureDetectorDelegate?

    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()
    private var lastTriggerMs: Int64 = 0

    public init() {}

    public func start() {
        guard motionManager.isAccelerometerAvailable else { return }
        motionManager.accelerometerUpdateInterval = 0.05
        motionManager.startAccelerometerUpdates(to: queue) { [weak self] data, error in
            guard let self = self, let accel = data?.acceleration else { return }
            let totalAccel = sqrt(accel.x * accel.x + accel.y * accel.y + accel.z * accel.z)
            guard totalAccel > Self.shakeThresholdG else { return }
            let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
            guard Self.shouldFire(nowMs: nowMs, lastTriggerMs: self.lastTriggerMs) else { return }
            self.lastTriggerMs = nowMs
            DispatchQueue.main.async {
                self.delegate?.gestureDetectorDidDetectManualTrigger(self)
            }
        }
    }

    public func stop() {
        if motionManager.isAccelerometerActive {
            motionManager.stopAccelerometerUpdates()
        }
    }

    public static func shouldFire(nowMs: Int64, lastTriggerMs: Int64) -> Bool {
        lastTriggerMs == 0 || nowMs - lastTriggerMs >= debounceMs
    }
}
#else
public final class GestureDetectorEngine {
    public static let shakeThresholdG: Double = 2.8
    public static let debounceMs: Int64 = 1_500
    public weak var delegate: GestureDetectorDelegate?
    public init() {}
    public func start() {}
    public func stop() {}
    public static func shouldFire(nowMs: Int64, lastTriggerMs: Int64) -> Bool {
        lastTriggerMs == 0 || nowMs - lastTriggerMs >= debounceMs
    }
}
#endif
