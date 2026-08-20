//
//  PoseTracker.swift
//  Akshrava iOS
//

import Foundation

public protocol PoseTrackerDelegate: AnyObject {
    func poseTracker(_ tracker: PoseTracker, didDetectExtremeTilt pitchCdeg: Int)
}

#if os(iOS)
import CoreMotion

public class PoseTracker {
    public weak var delegate: PoseTrackerDelegate?
    
    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()
    
    public private(set) var currentPitchCdeg: Int = 0
    public private(set) var currentRollCdeg: Int = 0
    public private(set) var poseAgeMs: Int = 999
    
    private var lastUpdateMonoMs: Int64 = 0
    private var extremeSinceMonoMs: Int64?
    private var lastAnnounceMonoMs: Int64 = 0
    
    public static let extremePitchCdeg = 4500
    public static let tiltHoldMs: Int64 = 2000
    public static let tiltCooldownMs: Int64 = 8000
    
    public init() {}
    
    public func start() {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 0.02 // 50 Hz
        motionManager.startDeviceMotionUpdates(to: queue) { [weak self] motion, error in
            guard let self = self, let motion = motion else { return }
            
            // Convert radians to centidegrees (1 rad = 5729.58 centidegrees)
            let pitchCdeg = Int(motion.attitude.pitch * 5729.58)
            let rollCdeg = Int(motion.attitude.roll * 5729.58)
            
            self.currentPitchCdeg = pitchCdeg
            self.currentRollCdeg = rollCdeg
            
            let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
            self.lastUpdateMonoMs = nowMs
            
            // Extreme tilt check (> 45 degrees pitch = 4500 centidegrees with 2s hold and 8s cooldown)
            if abs(pitchCdeg) > Self.extremePitchCdeg {
                let since = self.extremeSinceMonoMs ?? nowMs
                self.extremeSinceMonoMs = since
                if nowMs - since >= Self.tiltHoldMs && (self.lastAnnounceMonoMs == 0 || nowMs - self.lastAnnounceMonoMs >= Self.tiltCooldownMs) {
                    self.lastAnnounceMonoMs = nowMs
                    DispatchQueue.main.async {
                        self.delegate?.poseTracker(self, didDetectExtremeTilt: pitchCdeg)
                    }
                }
            } else {
                self.extremeSinceMonoMs = nil
            }
        }
    }
    
    public func stop() {
        if motionManager.isDeviceMotionActive {
            motionManager.stopDeviceMotionUpdates()
        }
    }
    
    public func updateAge() {
        let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        poseAgeMs = Int(max(0, nowMs - lastUpdateMonoMs))
    }
}
#else
public class PoseTracker {
    public weak var delegate: PoseTrackerDelegate?
    public private(set) var currentPitchCdeg: Int = 0
    public private(set) var currentRollCdeg: Int = 0
    public private(set) var poseAgeMs: Int = 999
    public init() {}
    public func start() {}
    public func stop() {}
    public func updateAge() { poseAgeMs = 0 }
}
#endif
