//
//  PoseTracker.swift
//  Akshrava iOS
//

import Foundation
import CoreMotion

public protocol PoseTrackerDelegate: AnyObject {
    func poseTracker(_ tracker: PoseTracker, didDetectExtremeTilt pitchCdeg: Int)
}

public class PoseTracker {
    public weak var delegate: PoseTrackerDelegate?
    
    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()
    
    public private(set) var currentPitchCdeg: Int = 0
    public private(set) var currentRollCdeg: Int = 0
    public private(set) var poseAgeMs: Int = 999
    
    private var lastUpdateMonoMs: Int64 = 0
    
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
            
            // Extreme tilt check (> 45 degrees pitch = 4500 centidegrees)
            if abs(pitchCdeg) > 4500 {
                DispatchQueue.main.async {
                    self.delegate?.poseTracker(self, didDetectExtremeTilt: pitchCdeg)
                }
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
