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

    // Session/frame bookkeeping below is written and read from at least four independent
    // execution contexts: the CaptureController video-sample-buffer queue (didOutputFrame), the
    // main thread (Start/Stop, every ProtocolClientDelegate callback -- ProtocolClient dispatches
    // its delegate calls via DispatchQueue.main.async), the gesture-detector's motion queue (which
    // itself re-dispatches to main, but that is still a different thread than the video queue),
    // and whatever arbitrary queue AVCaptureDevice.requestAccess's completion runs on (Apple
    // documents this as unspecified, not guaranteed main). None of these fields had any
    // synchronization before: a write to `framePending` on main (e.g.
    // protocolClientDidSettleFrame) was not guaranteed to ever become visible to the video queue's
    // `guard !framePending`, which could wedge every subsequent frame with the socket still open --
    // indistinguishable from a dead app to a user who cannot see the screen. One lock, held only
    // for the field access itself, mirrors the discipline already used in ProtocolClient.
    private let stateLock = NSLock()

    private var _frameId: Int64 = 0
    private var frameId: Int64 {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _frameId }
        set { stateLock.lock(); _frameId = newValue; stateLock.unlock() }
    }

    private var _framePending = false
    private var framePending: Bool {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _framePending }
        set { stateLock.lock(); _framePending = newValue; stateLock.unlock() }
    }

    private var _lookRequested = false
    private var lookRequested: Bool {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _lookRequested }
        set { stateLock.lock(); _lookRequested = newValue; stateLock.unlock() }
    }

    private var _isSessionActive = false
    /// Public (not just internal) so the UI layer can drive its Start/Stop control off the
    /// session's actual state instead of shadowing it in a separate local bool that can desync
    /// from a terminal disconnect the UI never observed.
    public var isSessionActive: Bool {
        stateLock.lock(); defer { stateLock.unlock() }
        return _isSessionActive
    }
    private func setSessionActive(_ value: Bool) {
        stateLock.lock(); _isSessionActive = value; stateLock.unlock()
    }

    private var _provision = DeviceProvision()
    private var provision: DeviceProvision {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _provision }
        set { stateLock.lock(); _provision = newValue; stateLock.unlock() }
    }

    private var _consecutiveOccludedFrames = 0
    private var consecutiveOccludedFrames: Int {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _consecutiveOccludedFrames }
        set { stateLock.lock(); _consecutiveOccludedFrames = newValue; stateLock.unlock() }
    }

    private var _consecutiveGlaredFrames = 0
    private var consecutiveGlaredFrames: Int {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _consecutiveGlaredFrames }
        set { stateLock.lock(); _consecutiveGlaredFrames = newValue; stateLock.unlock() }
    }

    private var _lastOcclusionAnnounceMs: Int64?
    private var lastOcclusionAnnounceMs: Int64? {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _lastOcclusionAnnounceMs }
        set { stateLock.lock(); _lastOcclusionAnnounceMs = newValue; stateLock.unlock() }
    }

    private var _lastGlareAnnounceMs: Int64?
    private var lastGlareAnnounceMs: Int64? {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _lastGlareAnnounceMs }
        set { stateLock.lock(); _lastGlareAnnounceMs = newValue; stateLock.unlock() }
    }

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

        let endpoint = EndpointPolicy.resolveEndpoint(customURLString: provision.endpoint)
        guard endpoint.host?.hasSuffix(".invalid") != true else {
            // The unconfigured placeholder resolves to a host that can never exist (RFC 2606).
            // Checked before any hardware/session side effect starts: refusing after the camera
            // is already running would announce "provisioning required" while assistance looked
            // like it had started.
            AlertManager.shared.speak(messageKey: "provisioning_required", force: true)
            AgentDebugLog.log(message: "start_rejected reason=unconfigured_endpoint")
            return
        }

        setSessionActive(true)
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

        protocolClient.connect(url: endpoint, authToken: token)
    }

    public func stopSession() {
        guard isSessionActive else { return }
        setSessionActive(false)
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
        // A message key is a lookup token, never speech text. The delegate resolves it through
        // AlertManager so a missing preview cannot make the synthesizer say "vehicle_nearby".
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
        // Claim the slot before URLSession work begins. A result/error callback can arrive before
        // sendFrame returns; setting this only afterward allowed that callback to clear the slot
        // and then had this queue write true back, wedging every later frame.
        framePending = true
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
        if !sent {
            framePending = false
            if priority {
                AlertManager.shared.speakStatus("Look failed, try again", force: true)
            }
        }
    }

    public func captureController(_ controller: CaptureController, didEncounterStall error: Error) {
        // Announce only. CaptureController owns the restart decision and bounds its own retries;
        // restarting again from here raced two independent stop/start pairs on the same
        // AVCaptureSession and could leave it stopped.
        AgentDebugLog.log(message: "Camera stall: \(error.localizedDescription)")
        AlertManager.shared.speak(messageKey: "camera_stall", force: true)
        framePending = false
    }

    public func captureController(_ controller: CaptureController, didBecomeUnavailable error: Error) {
        // Terminal: CaptureController has already stopped itself (or never started). Speak once
        // and end the session cleanly rather than looping recovery on a camera that cannot be
        // recovered from here (permission denial, or stall retries exhausted).
        AgentDebugLog.log(message: "Camera unavailable: \(error.localizedDescription)")
        framePending = false
        AlertManager.shared.speakStatus(
            "Camera unavailable. Assistance cannot start. Use cane or guide.", force: true
        )
        stopSession()
    }

    public func captureControllerWasInterrupted(_ controller: CaptureController) {
        // A device event (another app took the camera, background/multitasking access revoked)
        // is not a deliberate user stop. Silence here is indistinguishable from a dead app to a
        // user who cannot see the screen -- announce and keep the session logically alive so a
        // resume can pick back up without the user having to press Start again.
        framePending = false
        AlertManager.shared.speakStatus("Camera interrupted. Assistance paused. Use cane or guide.", force: true)
    }

    public func captureControllerDidResumeFromInterruption(_ controller: CaptureController) {
        AlertManager.shared.speakStatus("Camera resumed.", force: true)
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
            setSessionActive(false)
            SessionFlags.setActive(false)
            return
        }
        AlertManager.shared.speak(messageKey: "connection_dropped", force: true)
    }

    public func protocolClient(_ client: ProtocolClient, didReceiveResult payload: [String: Any]) {
        if let spoken = Self.speechText(forResult: payload) {
            AlertManager.shared.speakHazardPreview(spoken.text, language: provision.language)
            if spoken.urgent { hapticEngine.triggerUrgent() }
            else { hapticEngine.triggerCaution() }
            return
        }
        guard let hazard = payload["hazard"] as? [String: Any],
              let messageKey = hazard["message_key"] as? String,
              !messageKey.isEmpty else { return }
        AlertManager.shared.speakHazard(messageKey: messageKey, language: provision.language)
        if (hazard["level"] as? String) == "urgent" || (hazard["severity"] as? String) == "S1" {
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
