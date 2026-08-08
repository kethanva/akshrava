//
//  PoseTracker.swift
//  Akshrava iOS
//

import Foundation
#if os(iOS)
import CoreMotion
#endif

public protocol PoseTrackerDelegate: AnyObject {
    func poseTracker(_ tracker: PoseTracker, didDetectExtremeTilt pitchCdeg: Int)
}

public final class PoseTracker {
    public weak var delegate: PoseTrackerDelegate?

    public private(set) var currentPitchCdeg: Int = 0
    public private(set) var currentRollCdeg: Int = 0
    public private(set) var poseAgeMs: Int = 999

    private var lastUpdateMonoMs: Int64 = 0
    #if os(iOS)
    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()
    #endif

    public init() {}

    public func start() {
        #if os(iOS)
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 0.02 // 50 Hz
        motionManager.startDeviceMotionUpdates(to: queue) { [weak self] motion, error in
            guard let self = self, let motion = motion else { return }
            let pitchCdeg = Int(motion.attitude.pitch * 5729.58)
            let rollCdeg = Int(motion.attitude.roll * 5729.58)
            self.currentPitchCdeg = pitchCdeg
            self.currentRollCdeg = rollCdeg
            let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
            self.lastUpdateMonoMs = nowMs
            if abs(pitchCdeg) > 4500 {
                DispatchQueue.main.async {
                    self.delegate?.poseTracker(self, didDetectExtremeTilt: pitchCdeg)
                }
            }
        }
        #endif
    }

    public func stop() {
        #if os(iOS)
        if motionManager.isDeviceMotionActive {
            motionManager.stopDeviceMotionUpdates()
        }
        #endif
    }

    public func updateAge() {
        let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        if lastUpdateMonoMs == 0 {
            poseAgeMs = 999
            return
        }
        poseAgeMs = Int(max(0, nowMs - lastUpdateMonoMs))
    }

    /// Test helper: inject a pose without CoreMotion (macOS CI / unit tests).
    public func applyTestPose(pitchCdeg: Int, rollCdeg: Int, nowMs: Int64? = nil) {
        currentPitchCdeg = pitchCdeg
        currentRollCdeg = rollCdeg
        lastUpdateMonoMs = nowMs ?? Int64(ProcessInfo.processInfo.systemUptime * 1000)
        poseAgeMs = 0
    }
}
