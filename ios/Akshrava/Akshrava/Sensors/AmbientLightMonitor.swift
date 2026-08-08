//
//  AmbientLightMonitor.swift
//  Akshrava iOS
//
//  Ambient light edge context (F-71) — mirrors Android AmbientLightMonitor.kt.
//  Pure step/classify are platform-agnostic; iOS samples lux via CoreMotion is unavailable,
//  so the camera ISO×exposure proxy feeds the same lux FSM when a device is provided.
//

import Foundation
#if os(iOS)
import AVFoundation
#endif

public enum AmbientLightLevel: Equatable {
    case dark
    case bright
}

public struct AmbientLightState: Equatable {
    public var established: AmbientLightLevel?
    public var candidate: AmbientLightLevel?
    public var candidateSinceMs: Int64
    public var lastAnnounceMs: Int64?

    public init(
        established: AmbientLightLevel? = nil,
        candidate: AmbientLightLevel? = nil,
        candidateSinceMs: Int64 = 0,
        lastAnnounceMs: Int64? = nil
    ) {
        self.established = established
        self.candidate = candidate
        self.candidateSinceMs = candidateSinceMs
        self.lastAnnounceMs = lastAnnounceMs
    }
}

public struct AmbientLightStep: Equatable {
    public let state: AmbientLightState
    public let announce: AmbientLightLevel?

    public init(state: AmbientLightState, announce: AmbientLightLevel?) {
        self.state = state
        self.announce = announce
    }
}

public final class AmbientLightMonitor {
    public static let darkLux: Float = 10
    public static let brightLux: Float = 50
    public static let holdMs: Int64 = 3_000
    public static let cooldownMs: Int64 = 8_000

    public static func classify(lux: Float) -> AmbientLightLevel? {
        if lux < darkLux { return .dark }
        if lux > brightLux { return .bright }
        return nil
    }

    public static func step(state: AmbientLightState, lux: Float, nowMs: Int64) -> AmbientLightStep {
        guard let level = classify(lux: lux) else {
            return AmbientLightStep(state: state, announce: nil)
        }

        guard let established = state.established else {
            var seeded = state
            seeded.established = level
            seeded.candidate = nil
            seeded.candidateSinceMs = 0
            return AmbientLightStep(state: seeded, announce: nil)
        }

        if level == established {
            var cleared = state
            cleared.candidate = nil
            cleared.candidateSinceMs = 0
            return AmbientLightStep(state: cleared, announce: nil)
        }

        let since = (state.candidate == level) ? state.candidateSinceMs : nowMs
        if nowMs - since < holdMs {
            var pending = state
            pending.candidate = level
            pending.candidateSinceMs = since
            return AmbientLightStep(state: pending, announce: nil)
        }

        let last = state.lastAnnounceMs
        let suppressed: Bool
        if let last = last {
            suppressed = nowMs - last < cooldownMs
        } else {
            suppressed = false
        }
        let settled = AmbientLightState(
            established: level,
            candidate: nil,
            candidateSinceMs: 0,
            lastAnnounceMs: suppressed ? last : nowMs
        )
        return AmbientLightStep(state: settled, announce: suppressed ? nil : level)
    }

    /// Maps camera exposure metrics into an approximate lux so the same FSM can run without
    /// a dedicated ambient-light sensor (common on donated / older iPhones).
    public static func approximateLux(iso: Float, exposureSeconds: Double) -> Float {
        let index = Double(iso) * exposureSeconds
        // Tuned so ISO×t ≈ 2.0 (prior low-light heuristic) lands near darkLux.
        return Float(darkLux * 2.0 / max(index, 0.0001))
    }

    public static func statusText(for level: AmbientLightLevel) -> String {
        switch level {
        case .dark: return "Environment is dark."
        case .bright: return "Brighter now."
        }
    }

    private var state = AmbientLightState()
    private let onEdge: (AmbientLightLevel) -> Void
    #if os(iOS)
    private var timer: Timer?
    private weak var device: AVCaptureDevice?
    #endif

    public init(onEdge: @escaping (AmbientLightLevel) -> Void) {
        self.onEdge = onEdge
    }

    /// Convenience for tests / macOS SPM — no sensor loop.
    public convenience init() {
        self.init(onEdge: { _ in })
    }

    @discardableResult
    public func start(samplingDevice device: Any? = nil) -> Bool {
        #if os(iOS)
        state = AmbientLightState()
        self.device = device as? AVCaptureDevice
        timer?.invalidate()
        // 1 Hz — same budget as Android TYPE_LIGHT sample period.
        timer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.sample()
        }
        if let timer = timer {
            RunLoop.main.add(timer, forMode: .common)
        }
        return true
        #else
        return false
        #endif
    }

    public func stop() {
        #if os(iOS)
        timer?.invalidate()
        timer = nil
        device = nil
        #endif
        state = AmbientLightState()
    }

    /// Feed a lux reading (tests and alternate sensors).
    public func ingestLux(_ lux: Float, nowMs: Int64) {
        let stepped = Self.step(state: state, lux: lux, nowMs: nowMs)
        state = stepped.state
        if let announce = stepped.announce {
            onEdge(announce)
        }
    }

    #if os(iOS)
    private func sample() {
        guard let device = device else { return }
        let lux = Self.approximateLux(iso: device.iso, exposureSeconds: device.exposureDuration.seconds)
        let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        ingestLux(lux, nowMs: nowMs)
    }
    #endif
}
