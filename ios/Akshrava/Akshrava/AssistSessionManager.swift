//
//  AssistSessionManager.swift
//  Akshrava iOS
//
//  Main session coordinator — iOS equivalent of Android AssistService.kt.
//

import Foundation
#if os(iOS)
import AVFoundation
#endif

public final class AssistSessionManager {
    public static let shared = AssistSessionManager()

    public static let occludedFramesBeforeAnnounce = 3
    public static let glaredFramesBeforeAnnounce = 3
    public static let gateAnnounceCooldownMs: Int64 = 8_000

    private let capturePolicy = CapturePolicy()
    private let poseTracker = PoseTracker()
    private let protocolClient = ProtocolClient()
    private let hapticEngine = HapticFeedbackEngine()
    private let gestureDetector = GestureDetectorEngine()
    private var ambientLightMonitor: AmbientLightMonitor?
    private var screenKeepAlive = ScreenKeepAlive()

    #if os(iOS)
    private let captureController = CaptureController()
    private let frameEncoder = FrameEncoder()
    private let frameGate = FrameGate()
    private var captureDevice: AVCaptureDevice?
    #endif

    private var frameId: Int64 = 0
    private var framePending = false
    private var lookRequested = false
    private(set) var isSessionActive = false
    private var provision = DeviceProvision()
    private var consecutiveOccludedFrames = 0
    private var consecutiveGlaredFrames = 0
    private var lastOcclusionAnnounceMs: Int64?
    private var lastGlareAnnounceMs: Int64?

    private init() {
        #if os(iOS)
        captureController.delegate = self
        #endif
        poseTracker.delegate = self
        protocolClient.delegate = self
        gestureDetector.delegate = self
    }

    public func startSession() {
        // Duplicate Start on a healthy live session must be ignored. A terminally dead client
        // (auth revoked / 4401/4403) must rebuild — Start is the only recovery the user has.
        if isSessionActive && !protocolClient.isTerminal() {
            return
        }
        if isSessionActive {
            stopSession()
        }

        if let bundled = ProvisionStore.loadFromBundleProvisionJSON() {
            provision = bundled
        } else {
            provision = ProvisionStore.load()
        }

        let token = provision.deviceToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty else {
            AlertManager.shared.speak(messageKey: "provisioning_required", force: true)
            AgentDebugLog.log(message: "start_rejected reason=missing_token")
            return
        }

        isSessionActive = true
        SessionFlags.setActive(true)
        framePending = false
        lookRequested = false
        frameId = 0
        consecutiveOccludedFrames = 0
        consecutiveGlaredFrames = 0
        #if os(iOS)
        frameGate.resetBlurTracking()
        #endif

        poseTracker.start()
        gestureDetector.start()
        _ = screenKeepAlive.start()

        ambientLightMonitor = AmbientLightMonitor { [weak self] level in
            guard let self = self else { return }
            if AlertManager.shared.hazardSpokenWithin(ms: 2_500) { return }
            AlertManager.shared.speakStatus(AmbientLightMonitor.statusText(for: level), force: true)
        }
        #if os(iOS)
        captureDevice = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
        _ = ambientLightMonitor?.start(samplingDevice: captureDevice)
        captureController.startCapture()
        #else
        _ = ambientLightMonitor?.start()
        #endif

        let endpoint = EndpointPolicy.resolveEndpoint(customURLString: provision.endpoint)
        protocolClient.connect(url: endpoint, authToken: token)
    }

    public func stopSession() {
        guard isSessionActive else { return }
        isSessionActive = false
        SessionFlags.setActive(false)
        framePending = false
        lookRequested = false

        #if os(iOS)
        captureController.stopCapture()
        #endif
        ambientLightMonitor?.stop()
        ambientLightMonitor = nil
        poseTracker.stop()
        gestureDetector.stop()
        protocolClient.disconnect()
        screenKeepAlive.stop()
    }

    /// Testable gate: announce occlusion only after consecutive dark frames + cooldown.
    public static func shouldAnnounceOcclusion(
        consecutive: Int,
        nowMs: Int64,
        lastAnnounceMs: Int64?
    ) -> Bool {
        guard consecutive >= occludedFramesBeforeAnnounce else { return false }
        guard let last = lastAnnounceMs else { return true }
        return nowMs - last >= gateAnnounceCooldownMs
    }

    public static func shouldAnnounceGlare(
        consecutive: Int,
        nowMs: Int64,
        lastAnnounceMs: Int64?
    ) -> Bool {
        guard consecutive >= glaredFramesBeforeAnnounce else { return false }
        guard let last = lastAnnounceMs else { return true }
        return nowMs - last >= gateAnnounceCooldownMs
    }

    /// Pure speech selection for a fresh result (Android ProtocolClient result branch).
    /// Never invents a local "clear" string for look — uses server look_summary / spoken_preview.
    public static func speechText(forResult payload: [String: Any]) -> (text: String, urgent: Bool)? {
        let priority = ProtocolClient.jsonBool(payload["priority"])
        let hazard = payload["hazard"] as? [String: Any]
        let lookSummary = (payload["look_summary"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let hazardPreview = (hazard?["spoken_preview"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let level = hazard?["level"] as? String
        let severity = hazard?["severity"] as? String
        let urgent = level == "urgent" || severity == "S1"

        if priority {
            let text = !lookSummary.isEmpty ? lookSummary : hazardPreview
            guard !text.isEmpty else { return nil }
            return (text, true)
        }

        guard hazard != nil else { return nil }
        if !hazardPreview.isEmpty {
            return (hazardPreview, urgent)
        }
        if let messageKey = hazard?["message_key"] as? String, !messageKey.isEmpty {
            return (messageKey, urgent)
        }
        return nil
    }
}

#if os(iOS)
extension AssistSessionManager: CaptureControllerDelegate {
    public func captureController(_ controller: CaptureController,
                                   didOutputFrame sampleBuffer: CMSampleBuffer) {
        guard isSessionActive else { return }

        poseTracker.updateAge()
        SessionFlags.heartbeat()

        let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        let priority = lookRequested
        if !priority {
            guard capturePolicy.shouldCapture(isMoving: true, currentMonoMs: nowMs) else { return }
        }
        guard !framePending else { return }

        let blurred = frameGate.isBlurred(sampleBuffer: sampleBuffer)
        frameGate.noteBlurred(blurred)
        if frameGate.shouldAnnounceBlurPrompt(nowMs: nowMs) {
            AlertManager.shared.speak(messageKey: "camera_blur", force: true)
            frameGate.markBlurAnnounced(nowMs: nowMs)
        }

        switch frameGate.evaluate(sampleBuffer: sampleBuffer) {
        case .occluded:
            consecutiveOccludedFrames += 1
            consecutiveGlaredFrames = 0
            if Self.shouldAnnounceOcclusion(
                consecutive: consecutiveOccludedFrames,
                nowMs: nowMs,
                lastAnnounceMs: lastOcclusionAnnounceMs
            ) {
                lastOcclusionAnnounceMs = nowMs
                AlertManager.shared.speak(messageKey: "camera_dark", force: true)
            }
            return
        case .glared:
            consecutiveGlaredFrames += 1
            consecutiveOccludedFrames = 0
            if Self.shouldAnnounceGlare(
                consecutive: consecutiveGlaredFrames,
                nowMs: nowMs,
                lastAnnounceMs: lastGlareAnnounceMs
            ) {
                lastGlareAnnounceMs = nowMs
                AlertManager.shared.speak(messageKey: "camera_glare", force: true)
            }
            return
        case .pass:
            consecutiveOccludedFrames = 0
            consecutiveGlaredFrames = 0
        }

        guard let encoded = frameEncoder.encode(sampleBuffer: sampleBuffer) else { return }

        lookRequested = false
        frameId += 1
        let wire = EncodedFrameWire(
            jpegData: encoded.jpegData,
            width: encoded.width,
            height: encoded.height
        )
        let sent = protocolClient.sendFrame(
            frameId: frameId,
            captureMonoMs: encoded.captureMonoMs,
            pitchCdeg: poseTracker.currentPitchCdeg,
            rollCdeg: poseTracker.currentRollCdeg,
            poseAgeMs: poseTracker.poseAgeMs,
            frame: wire,
            calibrationId: provision.calibrationId,
            language: provision.language,
            mode: priority ? "priority" : "normal",
            priority: priority
        )
        if sent {
            framePending = true
        } else if priority {
            AlertManager.shared.speakStatus("Look failed, try again", force: true)
        }
    }

    public func captureController(_ controller: CaptureController, didEncounterStall error: Error) {
        AgentDebugLog.log(message: "Camera stall: \(error.localizedDescription)")
        AlertManager.shared.speak(messageKey: "camera_stall", force: true)
        framePending = false
        captureController.stopCapture()
        captureController.startCapture()
    }
}
#endif

extension AssistSessionManager: PoseTrackerDelegate {
    public func poseTracker(_ tracker: PoseTracker, didDetectExtremeTilt pitchCdeg: Int) {
        hapticEngine.triggerCaution()
        AlertManager.shared.speakStatus("Phone tilted. Point camera forward.", force: true)
    }
}

extension AssistSessionManager: ProtocolClientDelegate {
    public func protocolClientDidConnect(_ client: ProtocolClient) {
        ConnectionEarcons.playOpen()
        AlertManager.shared.speak(messageKey: "connection_open", force: true)
    }

    public func protocolClient(_ client: ProtocolClient, didDisconnectWithCode code: Int, reason: String?) {
        framePending = false
        ConnectionEarcons.playDropped()
        if client.isTerminal() {
            // Leave resources for Stop/Start rebuild, but mark inactive so Start is not ignored.
            isSessionActive = false
            SessionFlags.setActive(false)
            return
        }
        AlertManager.shared.speak(messageKey: "connection_dropped", force: true)
    }

    public func protocolClient(_ client: ProtocolClient, didReceiveResult payload: [String: Any]) {
        guard let spoken = Self.speechText(forResult: payload) else { return }
        AlertManager.shared.speakHazardPreview(spoken.text, language: provision.language)
        if spoken.urgent {
            hapticEngine.triggerUrgent()
        } else {
            hapticEngine.triggerCaution()
        }
    }

    public func protocolClient(_ client: ProtocolClient, didReceiveQuality qualityPayload: [String: Any]) {}

    public func protocolClient(_ client: ProtocolClient, didReceiveError errorPayload: [String: Any]) {
        framePending = false
        if let detail = errorPayload["detail"] as? String, !detail.isEmpty {
            AlertManager.shared.speakStatus(detail, force: true)
        } else if let code = errorPayload["code"] as? String {
            AlertManager.shared.speakStatus("Vision error: \(code). Use cane or guide.", force: true)
        }
    }

    public func protocolClientDidSettleFrame(_ client: ProtocolClient) {
        framePending = false
    }

    public func protocolClientVisionNotReady(_ client: ProtocolClient) {
        framePending = false
    }
}

extension AssistSessionManager: GestureDetectorDelegate {
    public func gestureDetectorDidDetectManualTrigger(_ engine: GestureDetectorEngine) {
        // Mirror Android: acknowledge look, queue one priority frame — never invent "clear".
        lookRequested = true
        hapticEngine.triggerCaution()
        AlertManager.shared.speakStatus("Looking.", force: true)
    }
}
