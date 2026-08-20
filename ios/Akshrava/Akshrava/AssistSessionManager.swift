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
    public static let sessionStateDidChangeNotification = Notification.Name(
        "org.akshrava.ios.session-state-did-change"
    )

    public static let occludedFramesBeforeAnnounce = 3
    public static let glaredFramesBeforeAnnounce = 3
    public static let gateAnnounceCooldownMs: Int64 = 8_000
    /// Prefixes, not whole words: a deny-list must also catch "safely", "collisions", "navigating".
    /// ASCII/English-only; non-Latin renderings are covered by server composer parity tests.
    public static let forbiddenAwarenessPrefixes = ["saf", "clear", "cross", "navigat", "collis", "approach"]

    private let capturePolicy = CapturePolicy()
    private let poseTracker = PoseTracker()
    private let protocolClient = ProtocolClient()
    private let hapticEngine = HapticFeedbackEngine()
    private let gestureDetector = GestureDetectorEngine()
    private var consecutiveSoftSheds = 0
    private var lastSoftShedAnnounceMs: Int64 = 0
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
    private var _sessionGeneration: UInt64 = 0
    /// Public (not just internal) so the UI layer can drive its Start/Stop control off the
    /// session's actual state instead of shadowing it in a separate local bool that can desync
    /// from a terminal disconnect the UI never observed.
    public var isSessionActive: Bool {
        stateLock.lock(); defer { stateLock.unlock() }
        return _isSessionActive
    }
    private func postSessionStateChanged() {
        DispatchQueue.main.async {
            NotificationCenter.default.post(
                name: Self.sessionStateDidChangeNotification,
                object: self
            )
        }
    }

    /// Publish a fresh session and reset every frame-owned field in one transaction. A camera
    /// callback from an older run may still be encoding while Stop/Start rebuilds resources; its
    /// generation must become stale before the new run can claim an in-flight slot.
    private func beginSessionState() -> UInt64 {
        stateLock.lock()
        _sessionGeneration &+= 1
        let generation = _sessionGeneration
        let changed = !_isSessionActive
        _isSessionActive = true
        _framePending = false
        _lookRequested = false
        _frameId = 0
        _consecutiveOccludedFrames = 0
        _consecutiveGlaredFrames = 0
        _lastOcclusionAnnounceMs = nil
        _lastGlareAnnounceMs = nil
        #if os(iOS)
        frameGate.resetBlurTracking()
        #endif
        stateLock.unlock()
        consecutiveSoftSheds = 0
        lastSoftShedAnnounceMs = 0
        if changed { postSessionStateChanged() }
        return generation
    }

    /// End the current generation synchronously, before camera/socket teardown is queued.
    @discardableResult
    private func endSessionState() -> Bool {
        stateLock.lock()
        guard _isSessionActive else {
            stateLock.unlock()
            return false
        }
        _sessionGeneration &+= 1
        _isSessionActive = false
        _framePending = false
        _lookRequested = false
        stateLock.unlock()
        postSessionStateChanged()
        return true
    }

    private var _transportOutageActive = false
    private var _visionOutageActive = false
    private var _hasConnectedThisSession = false

    private enum ConnectionAnnouncement { case connected, restored, none }

    private func resetOutageState() {
        stateLock.lock()
        _transportOutageActive = false
        _visionOutageActive = false
        _hasConnectedThisSession = false
        stateLock.unlock()
    }

    private func connectionBecameReady() -> ConnectionAnnouncement {
        stateLock.lock()
        defer { stateLock.unlock() }
        // A ready handshake proves the transport, not inference. Suppress a premature
        // "restored" while a sustained inference outage is waiting for a real result.
        if _visionOutageActive {
            _transportOutageActive = false
            _hasConnectedThisSession = true
            return .none
        }
        if _transportOutageActive {
            _transportOutageActive = false
            _hasConnectedThisSession = true
            return .restored
        }
        if !_hasConnectedThisSession {
            _hasConnectedThisSession = true
            return .connected
        }
        return .none
    }

    private func markTransportOutage() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        let shouldAnnounce = !_transportOutageActive && !_visionOutageActive
        _transportOutageActive = true
        return shouldAnnounce
    }

    private func markVisionOutage() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        let shouldAnnounce = !_visionOutageActive
        _visionOutageActive = true
        return shouldAnnounce
    }

    private func recoverVisionOutage() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        let recovered = _visionOutageActive
        _visionOutageActive = false
        _transportOutageActive = false
        if recovered { _hasConnectedThisSession = true }
        return recovered
    }

    private var _provision = DeviceProvision()
    private var provision: DeviceProvision {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _provision }
        set { stateLock.lock(); _provision = newValue; stateLock.unlock() }
    }

    private var _consecutiveOccludedFrames = 0
    private var _consecutiveGlaredFrames = 0
    private var _lastOcclusionAnnounceMs: Int64?
    private var _lastGlareAnnounceMs: Int64?

    private struct FrameSendClaim {
        let frameId: Int64
        let priority: Bool
        let provision: DeviceProvision
    }

    private func frameDecisionSnapshot() -> (active: Bool, pending: Bool, look: Bool, generation: UInt64) {
        stateLock.lock()
        defer { stateLock.unlock() }
        return (_isSessionActive, _framePending, _lookRequested, _sessionGeneration)
    }

    /// Atomically re-check the session, consume one look request, allocate the frame ID, and
    /// claim the phone-side in-flight slot after encoding. Stop/error callbacks may run while the
    /// video queue is encoding; individual locked properties are not enough for this
    /// read-decide-write transaction.
    private func claimFrameForSend(generation: UInt64) -> FrameSendClaim? {
        stateLock.lock()
        defer { stateLock.unlock() }
        guard _isSessionActive, generation == _sessionGeneration, !_framePending else { return nil }
        _frameId += 1
        let claim = FrameSendClaim(
            frameId: _frameId,
            priority: _lookRequested,
            provision: _provision
        )
        _lookRequested = false
        _framePending = true
        return claim
    }

    private func releaseFrame(generation: UInt64) {
        stateLock.lock()
        if _isSessionActive, generation == _sessionGeneration {
            _framePending = false
        }
        stateLock.unlock()
    }

    private func isCurrentSession(generation: UInt64) -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return _isSessionActive && generation == _sessionGeneration
    }

    #if os(iOS)
    private enum QualityGateDecision {
        case pass
        case occluded(announce: Bool)
        case glared(announce: Bool)
    }

    private func updateBlurTracking(
        generation: UInt64,
        blurred: Bool,
        nowMs: Int64
    ) -> Bool? {
        stateLock.lock()
        defer { stateLock.unlock() }
        guard _isSessionActive, generation == _sessionGeneration else { return nil }
        frameGate.noteBlurred(blurred)
        let announce = frameGate.shouldAnnounceBlurPrompt(nowMs: nowMs)
        if announce { frameGate.markBlurAnnounced(nowMs: nowMs) }
        return announce
    }

    /// Apply quality-gate counters only to the session that produced the sample. Resetting these
    /// through individual locked properties still allowed an old video callback to increment the
    /// counters immediately after a new Start reset them.
    private func recordQualityGate(
        _ result: FrameGateResult,
        generation: UInt64,
        nowMs: Int64
    ) -> QualityGateDecision? {
        stateLock.lock()
        defer { stateLock.unlock() }
        guard _isSessionActive, generation == _sessionGeneration else { return nil }
        switch result {
        case .occluded:
            _consecutiveOccludedFrames += 1
            _consecutiveGlaredFrames = 0
            let announce = Self.shouldAnnounceOcclusion(
                consecutive: _consecutiveOccludedFrames,
                nowMs: nowMs,
                lastAnnounceMs: _lastOcclusionAnnounceMs
            )
            if announce { _lastOcclusionAnnounceMs = nowMs }
            return .occluded(announce: announce)
        case .glared:
            _consecutiveGlaredFrames += 1
            _consecutiveOccludedFrames = 0
            let announce = Self.shouldAnnounceGlare(
                consecutive: _consecutiveGlaredFrames,
                nowMs: nowMs,
                lastAnnounceMs: _lastGlareAnnounceMs
            )
            if announce { _lastGlareAnnounceMs = nowMs }
            return .glared(announce: announce)
        case .pass:
            _consecutiveOccludedFrames = 0
            _consecutiveGlaredFrames = 0
            return .pass
        }
    }
    #endif

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
        guard provision.isReady else {
            AlertManager.shared.speak(messageKey: "provisioning_required", force: true)
            AgentDebugLog.log(message: "start_rejected reason=incomplete_provisioning")
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

        let activeGeneration = beginSessionState()
        resetOutageState()
        SessionFlags.setActive(true)

        poseTracker.start()
        gestureDetector.start()
        _ = screenKeepAlive.start()

        ambientLightMonitor = AmbientLightMonitor { [weak self] level in
            guard let self = self, self.isCurrentSession(generation: activeGeneration) else { return }
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
        guard endSessionState() else { return }
        resetOutageState()
        SessionFlags.setActive(false)

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

    /// A persisted heartbeat can be stale while this process still has an in-memory manager that
    /// calls itself active. A normal duplicate Start intentionally does nothing; watchdog recovery
    /// must tear down that half-live state first so camera, socket, and timers are rebuilt.
    public func restartSessionAfterWatchdogStall() {
        if isSessionActive { stopSession() }
        startSession()
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

    /// Fail closed: only speak server text this client can positively vouch for.
    public static func awarenessTextIsSpeakable(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        let lower = trimmed.lowercased()
        return !forbiddenAwarenessPrefixes.contains { lower.contains($0) }
    }

    /// Look utterance matches Android: look_summary, else hazard.spoken_preview.
    public static func lookUtterance(from payload: [String: Any]) -> String {
        let lookSummary = (payload["look_summary"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if !lookSummary.isEmpty { return lookSummary }
        let hazard = payload["hazard"] as? [String: Any]
        return (hazard?["spoken_preview"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    /// A look whose utterance was non-empty but forbidden must speak look-unavailable, not the hazard.
    public static func lookWasRejected(_ payload: [String: Any]) -> Bool {
        guard ProtocolClient.jsonBool(payload["priority"]) else { return false }
        let text = lookUtterance(from: payload)
        guard !text.isEmpty else { return false }
        return !awarenessTextIsSpeakable(text)
    }

    /// Pure speech selection for a fresh result (Android ProtocolClient result branch).
    /// Never invents a local "clear" string for look — uses server look_summary / spoken_preview.
    public static func speechText(forResult payload: [String: Any]) -> (text: String, urgent: Bool)? {
        let priority = ProtocolClient.jsonBool(payload["priority"])
        let hazard = payload["hazard"] as? [String: Any]
        let hazardPreview = (hazard?["spoken_preview"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let level = hazard?["level"] as? String
        let severity = hazard?["severity"] as? String
        let urgent = level == "urgent" || severity == "S1"

        if priority {
            let text = lookUtterance(from: payload)
            guard !text.isEmpty else { return nil }
            guard awarenessTextIsSpeakable(text) else { return nil }
            return (text, true)
        }

        guard hazard != nil else { return nil }
        if !hazardPreview.isEmpty {
            guard awarenessTextIsSpeakable(hazardPreview) else { return nil }
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
        let decision = frameDecisionSnapshot()
        guard decision.active else { return }

        poseTracker.updateAge()
        SessionFlags.heartbeat()

        // During initial connect or a reconnect, do not spend donated-phone CPU/heat on luma
        // analysis and JPEG encoding for a frame the protocol client will discard. A pending look
        // remains queued and is consumed only once streaming is genuinely ready.
        guard protocolClient.canStream() else { return }

        let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        let priority = decision.look
        if !priority {
            guard capturePolicy.shouldCapture(isMoving: true, currentMonoMs: nowMs) else { return }
        }
        guard !decision.pending else { return }

        let blurred = frameGate.isBlurred(sampleBuffer: sampleBuffer)
        guard let announceBlur = updateBlurTracking(
            generation: decision.generation,
            blurred: blurred,
            nowMs: nowMs
        ) else { return }
        if announceBlur, isCurrentSession(generation: decision.generation) {
            AlertManager.shared.speak(messageKey: "camera_blur", force: true)
        }

        let gateResult = frameGate.evaluate(sampleBuffer: sampleBuffer)
        guard let gateDecision = recordQualityGate(
            gateResult,
            generation: decision.generation,
            nowMs: nowMs
        ) else { return }
        switch gateDecision {
        case .occluded(let announce):
            if announce, isCurrentSession(generation: decision.generation) {
                AlertManager.shared.speak(messageKey: "camera_dark", force: true)
            }
            return
        case .glared(let announce):
            if announce, isCurrentSession(generation: decision.generation) {
                AlertManager.shared.speak(messageKey: "camera_glare", force: true)
            }
            // Glare, like blur, never drops a frame: a false washout verdict would stop
            // assistance in bright outdoor light.
        case .pass:
            break
        }

        guard let encoded = frameEncoder.encode(sampleBuffer: sampleBuffer) else { return }

        guard let claim = claimFrameForSend(generation: decision.generation) else { return }
        let wire = EncodedFrameWire(
            jpegData: encoded.jpegData,
            width: encoded.width,
            height: encoded.height
        )
        let sent = protocolClient.sendFrame(
            frameId: claim.frameId,
            captureMonoMs: encoded.captureMonoMs,
            pitchCdeg: poseTracker.currentPitchCdeg,
            rollCdeg: poseTracker.currentRollCdeg,
            poseAgeMs: poseTracker.poseAgeMs,
            frame: wire,
            calibrationId: claim.provision.calibrationId,
            language: claim.provision.language,
            mode: claim.priority ? "priority" : "normal",
            priority: claim.priority
        )
        if !sent {
            releaseFrame(generation: decision.generation)
        }
    }

    public func captureController(_ controller: CaptureController, didEncounterStall error: Error) {
        // Announce only. CaptureController owns the restart decision and bounds its own retries;
        // restarting again from here raced two independent stop/start pairs on the same
        // AVCaptureSession and could leave it stopped.
        AgentDebugLog.error(event: "camera_stall", detail: error.localizedDescription)
        AlertManager.shared.speak(messageKey: "camera_stall", force: true)
    }

    public func captureController(_ controller: CaptureController, didBecomeUnavailable error: Error) {
        // Terminal: CaptureController has already stopped itself (or never started). Speak once
        // and end the session cleanly rather than looping recovery on a camera that cannot be
        // recovered from here (permission denial, or stall retries exhausted).
        AgentDebugLog.error(event: "camera_unavailable", detail: error.localizedDescription)
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
        guard isSessionActive else { return }
        switch connectionBecameReady() {
        case .connected:
            ConnectionEarcons.playOpen()
            AlertManager.shared.speak(messageKey: "connection_open", force: true)
        case .restored:
            ConnectionEarcons.playRestored()
            AlertManager.shared.speak(messageKey: "connection_restored", force: true)
        case .none:
            break
        }
    }

    public func protocolClient(_ client: ProtocolClient, didDisconnectWithCode code: Int, reason: String?) {
        guard isSessionActive else { return }
        framePending = false
        if client.isTerminal() {
            // A terminal token/revocation failure cannot recover by itself. Stop camera, sensors,
            // screen keep-alive, and socket now; leaving those resources running after marking
            // the UI inactive burned battery and retained camera access indefinitely.
            stopSession()
            return
        }
        if markTransportOutage() {
            ConnectionEarcons.playDropped()
            AlertManager.shared.speak(messageKey: "connection_dropped", force: true)
        }
    }

    public func protocolClient(_ client: ProtocolClient, didReceiveResult payload: [String: Any]) {
        guard isSessionActive else { return }
        consecutiveSoftSheds = 0
        let recovered = recoverVisionOutage()
        var awarenessSpoken = false
        if Self.lookWasRejected(payload) {
            AlertManager.shared.speak(messageKey: "look_unavailable", language: provision.language, force: true)
            awarenessSpoken = true
        } else if let spoken = Self.speechText(forResult: payload) {
            AlertManager.shared.speakHazardPreview(spoken.text, language: provision.language)
            if spoken.urgent { hapticEngine.triggerUrgent() }
            else { hapticEngine.triggerCaution() }
            awarenessSpoken = true
        } else if let hazard = payload["hazard"] as? [String: Any],
                  let messageKey = hazard["message_key"] as? String,
                  !messageKey.isEmpty {
            AlertManager.shared.speakHazard(messageKey: messageKey, language: provision.language)
            if (hazard["level"] as? String) == "urgent" || (hazard["severity"] as? String) == "S1" {
                hapticEngine.triggerUrgent()
            } else {
                hapticEngine.triggerCaution()
            }
            awarenessSpoken = true
        }
        // Never put recovery speech or an earcon over the first fresh awareness result. A fresh
        // empty result still confirms the detector path recovered and gets an explicit status.
        if recovered && !awarenessSpoken {
            ConnectionEarcons.playRestored()
            AlertManager.shared.speakStatus("Vision assistance restored.", force: true)
        }
    }

    public func protocolClient(_ client: ProtocolClient, didReceiveQuality qualityPayload: [String: Any]) {
        guard isSessionActive else { return }
    }

    public func protocolClient(_ client: ProtocolClient, didReceiveError errorPayload: [String: Any]) {
        guard isSessionActive else { return }
        // Frame ownership is released only by protocolClientDidSettleFrame. Some errors (for
        // example malformed unowned server JSON) deliberately leave the exact-frame timeout
        // armed; clearing this second slot here let the camera encode repeatedly while
        // ProtocolClient still rejected every send as already in flight. Exact frame errors call
        // settleFrame first and therefore arrive with the settlement callback queued separately.
        guard let code = errorPayload["code"] as? String else { return }
        if ProtocolClient.silentSoftErrorCodes.contains(code) {
            consecutiveSoftSheds += 1
            let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
            if ProtocolClient.shouldAnnounceSoftShed(
                consecutiveSoftSheds: consecutiveSoftSheds,
                lastAnnounceAtMonoMs: lastSoftShedAnnounceMs,
                nowMonoMs: nowMs
            ) {
                lastSoftShedAnnounceMs = nowMs
                AlertManager.shared.speak(messageKey: "server_shedding", language: provision.language, force: true)
            }
            return
        }
        switch code {
        case "inference_circuit_open", "vision_unavailable":
            if markVisionOutage() {
                AlertManager.shared.speakStatus(
                    "Vision assistance unavailable. Use cane or guide.", force: true
                )
            }
        case "inference_restored":
            if recoverVisionOutage() {
                ConnectionEarcons.playRestored()
                AlertManager.shared.speakStatus("Vision assistance restored.", force: true)
            }
        case "provisioning_required":
            AlertManager.shared.speak(messageKey: "provisioning_required", force: true)
        case "invalid_server_response", "protocol_error", "protocol_violation":
            if markVisionOutage() {
                AlertManager.shared.speakStatus(
                    "Vision service response invalid. Use cane or guide.", force: true
                )
            }
        default:
            if markVisionOutage() {
                AlertManager.shared.speakStatus(
                    "Vision assistance unavailable. Use cane or guide.", force: true
                )
            }
        }
    }

    public func protocolClientDidSettleFrame(_ client: ProtocolClient) {
        guard isSessionActive else { return }
        framePending = false
    }

    public func protocolClientVisionNotReady(_ client: ProtocolClient) {
        guard isSessionActive else { return }
        framePending = false
        // Only a `ready` frame with vision_enabled=false (bench/noop) should reach here.
        // Send-time vision_not_ready drops the JPEG and keeps reconnecting.
        stopSession()
    }
}

extension AssistSessionManager: GestureDetectorDelegate {
    public func gestureDetectorDidDetectManualTrigger(_ engine: GestureDetectorEngine) {
        // Mirror Android: acknowledge look, queue one priority frame — never invent "clear".
        guard isSessionActive, protocolClient.canStream() else {
            hapticEngine.triggerCaution()
            AlertManager.shared.speakStatus("Look failed. Try again.", force: true)
            return
        }
        lookRequested = true
        hapticEngine.triggerCaution()
        AlertManager.shared.speakStatus("Looking.", force: true)
    }
}
