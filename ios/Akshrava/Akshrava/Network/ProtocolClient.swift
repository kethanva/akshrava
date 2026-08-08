//
//  ProtocolClient.swift
//  Akshrava iOS
//
//  WebSocket control plane — mirrors Android ProtocolClient.kt wire framing.
//

import Foundation

public protocol ProtocolClientDelegate: AnyObject {
    func protocolClientDidConnect(_ client: ProtocolClient)
    func protocolClient(_ client: ProtocolClient, didDisconnectWithCode code: Int, reason: String?)
    func protocolClient(_ client: ProtocolClient, didReceiveResult payload: [String: Any])
    func protocolClient(_ client: ProtocolClient, didReceiveQuality qualityPayload: [String: Any])
    func protocolClient(_ client: ProtocolClient, didReceiveError errorPayload: [String: Any])
    func protocolClientDidSettleFrame(_ client: ProtocolClient)
    func protocolClientVisionNotReady(_ client: ProtocolClient)
}

public extension ProtocolClientDelegate {
    func protocolClientDidSettleFrame(_ client: ProtocolClient) {}
    func protocolClientVisionNotReady(_ client: ProtocolClient) {}
}

public struct EncodedFrameWire {
    public let jpegData: Data
    public let width: Int
    public let height: Int

    public init(jpegData: Data, width: Int, height: Int) {
        self.jpegData = jpegData
        self.width = width
        self.height = height
    }
}

public final class ProtocolClient: NSObject, URLSessionWebSocketDelegate, URLSessionTaskDelegate {
    public weak var delegate: ProtocolClientDelegate?

    public static let poseCdegMin = -18_000
    public static let poseCdegMax = 18_000
    /// Older servers fatal-close on pose < -9000. Omit those values unless the server advertises full range.
    public static let legacyPoseCdegFloor = -9_000
    public static let capabilityPoseCdegFullRange = "pose_cdeg_full_range"
    public static let capabilityResultAcknowledgement = "result_acknowledgement"
    public static let frameSettleTimeoutMs: Int64 = 10_000
    public static let settleTimeoutsBeforeReconnect = 2
    public static let staleInferenceTickAfterMs: Int64 = 3_000
    public static let staleInferenceTickPeriodMs: Int64 = 2_000
    public static let staleInferenceMaxTicks = 3
    public static let staleAlertMs: Int64 = 2_500
    public static let lookFreshnessMs: Int64 = 2_500
    public static let urgentFreshnessMs: Int64 = 1_500
    /// How often a keep-alive ping is sent to renew the server session admission lease (60 s).
    /// Three pings must fit inside the 180 s server lease window — SessionDurationTests pins this.
    public static let appPingIntervalMs: Int64 = 60_000
    /// Reconnect backoff bounds. Capped exponential with full jitter so a fleet-wide event (a
    /// Cloud Run revision rollout) does not make every phone retry in lockstep every 2 s.
    public static let reconnectBaseSeconds: Double = 1.0
    public static let reconnectMaxSeconds: Double = 60.0

    public static func clampPoseCdeg(_ value: Int) -> Int {
        min(max(value, poseCdegMin), poseCdegMax)
    }

    /// Phone-owned result age: echo of capture_mono_ms vs current mono clock (Android ProtocolClient).
    public static func resultAgeMs(captureMonoMs: Int64?, nowMs: Int64) -> Int64 {
        guard let capture = captureMonoMs, capture >= 0 else { return Int64.max }
        return nowMs - capture
    }

    public static func maxSpeakAgeMs(
        priority: Bool,
        isUrgent: Bool,
        configuredStaleAlertMs: Int64
    ) -> Int64 {
        if priority { return max(lookFreshnessMs, configuredStaleAlertMs) }
        if isUrgent { return max(urgentFreshnessMs, configuredStaleAlertMs) }
        return configuredStaleAlertMs
    }

    public static func jsonInt64(_ value: Any?) -> Int64? {
        if let number = value as? NSNumber { return number.int64Value }
        if let int = value as? Int { return Int64(int) }
        if let int64 = value as? Int64 { return int64 }
        return nil
    }

    public static func jsonBool(_ value: Any?) -> Bool {
        if let bool = value as? Bool { return bool }
        if let number = value as? NSNumber { return number.boolValue }
        return false
    }

    /// Null means omit the field. Fail closed: unknown servers keep the legacy omit-below-floor behaviour.
    public static func wirePoseCdeg(_ value: Int, serverAcceptsFullPoseRange: Bool = false) -> Int? {
        let clamped = clampPoseCdeg(value)
        if serverAcceptsFullPoseRange { return clamped }
        return clamped < legacyPoseCdegFloor ? nil : clamped
    }

    public static func parseCapabilities(_ payload: [String: Any]) -> Set<String> {
        guard let array = payload["capabilities"] as? [Any] else { return [] }
        var found = Set<String>()
        for item in array {
            if let s = item as? String, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                found.insert(s)
            }
        }
        return found
    }

    public static func shouldTickStaleInference(inFlight: Bool, frameAgeMs: Int64, ticksAlready: Int) -> Bool {
        inFlight && frameAgeMs >= staleInferenceTickAfterMs && ticksAlready < staleInferenceMaxTicks
    }

    /// Capped exponential backoff with full jitter, in seconds. `attempt` is 1-based.
    public static func reconnectDelaySeconds(attempt: Int, random: Double = Double.random(in: 0...1)) -> Double {
        let capped = min(reconnectBaseSeconds * pow(2.0, Double(max(0, attempt - 1))), reconnectMaxSeconds)
        // Full jitter (0...capped), not a multiplier band: this is what actually decorrelates a
        // fleet that all dropped at the same instant, per the standard AWS backoff guidance.
        return capped * random
    }

    /// Terminal error codes the server can send in an "error" message body ahead of (or instead
    /// of) a close frame carrying an application close code. iOS's public WebSocket API cannot
    /// represent a close code outside 1000-1015 -- it collapses any such code to a generic
    /// "invalid" value and discards the number -- so a revoked/rejected session can only be
    /// reliably distinguished from an ordinary transport drop via this message body.
    public static let terminalErrorCodes: Set<String> = ["device_revoked", "authentication_failed"]

    /// Builds the JSON header dictionary that must precede the JPEG binary on the wire.
    public static func buildFrameHeader(
        frameId: Int64,
        captureMonoMs: Int64,
        captureEpochMs: Int64,
        frame: EncodedFrameWire,
        calibrationId: String,
        language: String,
        mode: String = "normal",
        priority: Bool = false,
        pitchCdeg: Int?,
        rollCdeg: Int?,
        poseAgeMs: Int?,
        serverAcceptsFullPoseRange: Bool
    ) -> [String: Any] {
        let look = priority || mode == "priority"
        // NSNumber / Int only — JSONSerialization rejects bare Int64 on some SDKs.
        var header: [String: Any] = [
            "type": "frame",
            "id": NSNumber(value: frameId),
            "capture_mono_ms": NSNumber(value: captureMonoMs),
            "capture_epoch_ms": NSNumber(value: captureEpochMs),
            "w": frame.width,
            "h": frame.height,
            "jpeg_bytes": frame.jpegData.count,
            "camera_calibration_id": calibrationId,
            "mode": look ? "priority" : mode,
            "priority": look,
            "language": wireLanguage(language),
            "trace_id": "frame-\(frameId)-\(captureMonoMs)",
            "result_acknowledgement": true,
        ]
        if let pitch = pitchCdeg, let wired = wirePoseCdeg(pitch, serverAcceptsFullPoseRange: serverAcceptsFullPoseRange) {
            header["pitch_cdeg"] = wired
        }
        if let roll = rollCdeg, let wired = wirePoseCdeg(roll, serverAcceptsFullPoseRange: serverAcceptsFullPoseRange) {
            header["roll_cdeg"] = wired
        }
        if let age = poseAgeMs {
            header["pose_age_ms"] = max(0, age)
        }
        return header
    }

    public static func wireLanguage(_ language: String) -> String {
        let tag = language.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if tag.hasPrefix("hi") { return "hi" }
        if tag.hasPrefix("ta") { return "ta" }
        if tag.hasPrefix("kn") { return "kn" }
        if tag.hasPrefix("ml") { return "ml" }
        if tag.hasPrefix("te") { return "te" }
        return "en"
    }

    // All mutable instance state below is guarded by `stateLock`. This client is driven from at
    // least three independent execution contexts -- the URLSession delegate queue, the
    // DispatchQueue.global() timers (settle timeout, stale-inference watchdog, ping, reconnect),
    // and whatever thread calls the public API (sendFrame, connect, disconnect) -- and every one
    // of these fields has been read from one and written from another at some point in this
    // class's history. A single lock, held only for the field access itself and never across a
    // call to `send`/`openSocket`/the delegate, is simpler and safer here than trying to prove
    // which subset of fields is "queue-confined" and keeping that proof true under future edits.
    private let stateLock = NSLock()

    private var webSocketTask: URLSessionWebSocketTask?
    private var urlSession: URLSession?
    private var authToken: String = ""
    private var reconnectURL: URL?
    private var closedByUser = false
    private var connectionGeneration = 0
    /// The connectionGeneration a transport drop has already been handled for. listen()'s
    /// `.failure` case and the `didCloseWith` delegate callback can both fire for the same drop;
    /// this makes the first one authoritative and the second a no-op instead of double-scheduling
    /// a reconnect / double-flipping terminal state.
    private var dropHandledGeneration = -1

    private var sessionReady = false
    private var visionEnabled = false
    private var serverCapabilities: Set<String> = []
    private var serverProtocolVersion = 0
    private var configuredStaleAlertMs: Int64 = staleAlertMs
    private var inFlight = false
    private var consecutiveSettleTimeouts = 0
    private var settleWorkItem: DispatchWorkItem?
    private var staleInferenceWorkItem: DispatchWorkItem?
    private var staleInferenceTicks = 0
    private var frameSentAtMonoMs: Int64 = 0
    private var receiveLoopActive = false
    private var permanentFailure = false
    private var pingWorkItem: DispatchWorkItem?
    private var reconnectAttempt = 0
    private var reconnectScheduled = false
    private let webSocketDispatchQueue = DispatchQueue(label: "org.akshrava.ios.ws.delegate")

    public override init() {
        super.init()
    }

    public func canStream() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return sessionReady && visionEnabled
    }

    public func serverAcceptsFullPoseRange() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return serverCapabilities.contains(Self.capabilityPoseCdegFullRange)
    }

    public func serverAcceptsResultAcknowledgements() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return serverCapabilities.contains(Self.capabilityResultAcknowledgement)
    }

    /// Protocol version advertised by the connected revision; 0 means "did not say".
    public func negotiatedProtocolVersion() -> Int {
        stateLock.lock()
        defer { stateLock.unlock() }
        return serverProtocolVersion
    }

    /// True after user stop or permanent auth failure — Start must rebuild, reconnect will not.
    public func isTerminal() -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return closedByUser || permanentFailure
    }

    private func isCurrentTask(_ task: URLSessionTask) -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return task === webSocketTask
    }

    /// Connect requires a non-blank JWT. Empty token fails closed — production WSS rejects anonymous sockets.
    public func connect(url: URL, authToken: String) {
        let trimmed = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            AgentDebugLog.log(message: "connect_rejected reason=missing_token")
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.delegate?.protocolClient(self, didReceiveError: [
                    "type": "error",
                    "code": "provisioning_required",
                    "detail": "Provisioning required",
                ])
            }
            return
        }
        stateLock.lock()
        closedByUser = false
        permanentFailure = false
        self.authToken = trimmed
        self.reconnectURL = url
        reconnectAttempt = 0
        reconnectScheduled = false
        stateLock.unlock()
        openSocket(url: url, token: trimmed, origin: "initial")
    }

    public func disconnect() {
        stateLock.lock()
        closedByUser = true
        receiveLoopActive = false
        let task = webSocketTask
        let session = urlSession
        webSocketTask = nil
        urlSession = nil
        stateLock.unlock()

        cancelSettleTimeout()
        cancelStaleInferenceWatchdog()
        cancelPing()
        settleFrame()
        clearNegotiatedState()
        task?.cancel(with: .normalClosure, reason: Data("user stopped".utf8))
        session?.invalidateAndCancel()
    }

    @discardableResult
    public func sendFrame(
        frameId: Int64,
        captureMonoMs: Int64,
        pitchCdeg: Int?,
        rollCdeg: Int?,
        poseAgeMs: Int?,
        frame: EncodedFrameWire,
        calibrationId: String,
        language: String = "en",
        mode: String = "normal",
        priority: Bool = false
    ) -> Bool {
        stateLock.lock()
        let task = webSocketTask
        guard task != nil else {
            stateLock.unlock()
            return failSendFrame(priority: priority, reason: "socket_missing")
        }
        guard sessionReady && visionEnabled else {
            stateLock.unlock()
            delegate?.protocolClientVisionNotReady(self)
            return failSendFrame(priority: priority, reason: "vision_not_ready")
        }
        if inFlight {
            stateLock.unlock()
            return failSendFrame(priority: priority, reason: "frame_in_flight")
        }
        inFlight = true
        let fullPose = serverCapabilities.contains(Self.capabilityPoseCdegFullRange)
        frameSentAtMonoMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        stateLock.unlock()

        let header = Self.buildFrameHeader(
            frameId: frameId,
            captureMonoMs: captureMonoMs,
            captureEpochMs: Int64(Date().timeIntervalSince1970 * 1000),
            frame: frame,
            calibrationId: calibrationId,
            language: language,
            mode: mode,
            priority: priority,
            pitchCdeg: pitchCdeg,
            rollCdeg: rollCdeg,
            poseAgeMs: poseAgeMs,
            serverAcceptsFullPoseRange: fullPose
        )

        guard JSONSerialization.isValidJSONObject(header),
              let headerData = try? JSONSerialization.data(withJSONObject: header),
              let headerString = String(data: headerData, encoding: .utf8) else {
            settleFrame()
            return failSendFrame(priority: priority, reason: "header_encode_failed")
        }

        let look = priority || mode == "priority"
        // Arm the settle timeout and stale-inference watchdog BEFORE either send, not after the
        // JPEG write completes. A slow or half-open link can leave `send` pending for tens of
        // seconds; during that whole window the client previously had a frame marked in-flight
        // with no timeout armed at all, so a wedged write had no recovery path until the app was
        // restarted.
        scheduleSettleTimeout(isLook: look)
        scheduleStaleInferenceWatchdog()

        task?.send(.string(headerString)) { [weak self] error in
            guard let self = self else { return }
            if let error = error {
                AgentDebugLog.log(message: "header_send_failed \(error.localizedDescription)")
                self.settleFrame()
                return
            }
            task?.send(.data(frame.jpegData)) { [weak self] error in
                guard let self = self else { return }
                if let error = error {
                    AgentDebugLog.log(message: "jpeg_send_failed \(error.localizedDescription)")
                    task?.cancel(with: .abnormalClosure, reason: Data("incomplete frame".utf8))
                    self.settleFrame()
                }
            }
        }
        return true
    }

    private func failSendFrame(priority: Bool, reason: String) -> Bool {
        AgentDebugLog.log(message: "frame_drop reason=\(reason)")
        if priority {
            AlertManager.shared.speakStatus("Look failed. Try again.", force: true)
        }
        return false
    }

    private func openSocket(url: URL, token: String, origin: String) {
        stateLock.lock()
        connectionGeneration += 1
        let generation = connectionGeneration
        let previousTask = webSocketTask
        let previousSession = urlSession
        stateLock.unlock()

        clearNegotiatedState()
        previousTask?.cancel(with: .goingAway, reason: nil)
        previousSession?.invalidateAndCancel()

        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        // A serial delegate queue is required here, not merely convenient: Apple documents that
        // URLSession delegate callbacks are expected to arrive serialized, but a bare
        // `OperationQueue()` defaults to concurrent execution. Without this, handleMessage,
        // settleFrame, handleTransportDrop, and every send completion handler could run
        // simultaneously over shared state -- which is exactly the failure this class's
        // lock-everything discipline exists to prevent, and a lock only helps if callers cannot
        // also reorder relative to each other across those callbacks.
        let delegateQueue = OperationQueue()
        delegateQueue.maxConcurrentOperationCount = 1
        delegateQueue.underlyingQueue = webSocketDispatchQueue
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: delegateQueue)

        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let task = session.webSocketTask(with: request)

        stateLock.lock()
        urlSession = session
        webSocketTask = task
        receiveLoopActive = true
        stateLock.unlock()

        task.resume()
        listen(generation: generation)
        schedulePing(generation: generation)
        AgentDebugLog.log(message: "connect_attempt origin=\(origin) attempt=\(generation)")
    }

    private func schedulePing(generation: Int) {
        cancelPing()
        let work = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            self.stateLock.lock()
            guard generation == self.connectionGeneration, !self.closedByUser else {
                self.stateLock.unlock()
                return
            }
            let task = self.webSocketTask
            self.stateLock.unlock()
            let body: [String: Any] = ["type": "ping"]
            if let data = try? JSONSerialization.data(withJSONObject: body),
               let text = String(data: data, encoding: .utf8) {
                task?.send(.string(text)) { _ in }
            }
            self.schedulePing(generation: generation)
        }
        stateLock.lock()
        pingWorkItem = work
        stateLock.unlock()
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.appPingIntervalMs)),
            execute: work
        )
    }

    private func cancelPing() {
        stateLock.lock()
        pingWorkItem?.cancel()
        pingWorkItem = nil
        stateLock.unlock()
    }

    private func clearNegotiatedState() {
        stateLock.lock()
        sessionReady = false
        visionEnabled = false
        serverCapabilities = []
        serverProtocolVersion = 0
        stateLock.unlock()
    }

    private func scheduleSettleTimeout(isLook: Bool) {
        cancelSettleTimeout()
        let generation = currentGeneration()
        let work = DispatchWorkItem { [weak self] in
            guard let self = self else { return }
            self.stateLock.lock()
            guard generation == self.connectionGeneration, !self.closedByUser else {
                self.stateLock.unlock()
                return
            }
            self.stateLock.unlock()
            if isLook {
                AlertManager.shared.speakStatus("Look failed. Try again.", force: true)
            }
            self.settleFrame()
            self.stateLock.lock()
            self.consecutiveSettleTimeouts += 1
            let shouldReconnect = self.consecutiveSettleTimeouts >= Self.settleTimeoutsBeforeReconnect
            if shouldReconnect { self.consecutiveSettleTimeouts = 0 }
            self.stateLock.unlock()
            if shouldReconnect {
                AgentDebugLog.log(message: "reconnect reason=repeated_settle_timeout")
                self.scheduleReconnect(origin: "settle_timeout")
            }
        }
        stateLock.lock()
        settleWorkItem = work
        stateLock.unlock()
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.frameSettleTimeoutMs)),
            execute: work
        )
    }

    private func cancelSettleTimeout() {
        stateLock.lock()
        settleWorkItem?.cancel()
        settleWorkItem = nil
        stateLock.unlock()
    }

    private func currentGeneration() -> Int {
        stateLock.lock()
        defer { stateLock.unlock() }
        return connectionGeneration
    }

    private func scheduleStaleInferenceWatchdog() {
        cancelStaleInferenceWatchdog()
        let generation = currentGeneration()
        stateLock.lock()
        staleInferenceTicks = 0
        stateLock.unlock()
        let work = DispatchWorkItem { [weak self] in
            self?.staleInferenceTick(generation: generation)
        }
        stateLock.lock()
        staleInferenceWorkItem = work
        stateLock.unlock()
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.staleInferenceTickAfterMs)),
            execute: work
        )
    }

    private func staleInferenceTick(generation: Int) {
        stateLock.lock()
        guard generation == connectionGeneration, !closedByUser else {
            stateLock.unlock()
            return
        }
        let flying = inFlight
        let sentAt = frameSentAtMonoMs
        let ticks = staleInferenceTicks
        stateLock.unlock()
        let now = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        let age = sentAt > 0 ? now - sentAt : 0
        var nextTicks = ticks
        if Self.shouldTickStaleInference(inFlight: flying, frameAgeMs: age, ticksAlready: ticks) {
            stateLock.lock()
            staleInferenceTicks += 1
            nextTicks = staleInferenceTicks
            stateLock.unlock()
            ConnectionEarcons.playDropped() // bounded tick — not an unbounded loop
            AgentDebugLog.log(message: "stale_inference_tick age=\(age) ticks=\(nextTicks)")
        }
        guard flying, nextTicks < Self.staleInferenceMaxTicks else { return }
        let work = DispatchWorkItem { [weak self] in
            self?.staleInferenceTick(generation: generation)
        }
        stateLock.lock()
        staleInferenceWorkItem = work
        stateLock.unlock()
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.staleInferenceTickPeriodMs)),
            execute: work
        )
    }

    private func cancelStaleInferenceWatchdog() {
        stateLock.lock()
        staleInferenceWorkItem?.cancel()
        staleInferenceWorkItem = nil
        staleInferenceTicks = 0
        stateLock.unlock()
    }

    private func settleFrame() {
        cancelSettleTimeout()
        cancelStaleInferenceWatchdog()
        stateLock.lock()
        let wasInFlight = inFlight
        inFlight = false
        stateLock.unlock()
        if wasInFlight {
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.delegate?.protocolClientDidSettleFrame(self)
            }
        }
    }

    private func listen(generation: Int) {
        stateLock.lock()
        let active = receiveLoopActive && generation == connectionGeneration
        let task = webSocketTask
        stateLock.unlock()
        guard active, let task = task else { return }
        task.receive { [weak self] result in
            guard let self = self else { return }
            self.stateLock.lock()
            let stillCurrent = generation == self.connectionGeneration
            self.stateLock.unlock()
            guard stillCurrent else { return }
            switch result {
            case .failure(let error):
                self.handleTransportDrop(code: 1006, reason: error.localizedDescription, generation: generation)
            case .success(let message):
                switch message {
                case .string(let text):
                    self.handleMessage(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.handleMessage(text)
                    }
                @unknown default:
                    break
                }
                self.stateLock.lock()
                let stillActive = self.receiveLoopActive
                self.stateLock.unlock()
                if stillActive {
                    self.listen(generation: generation)
                }
            }
        }
    }

    private func handleMessage(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            settleFrame()
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.delegate?.protocolClient(
                    self,
                    didReceiveError: ["type": "error", "code": "invalid_server_response"]
                )
            }
            return
        }

        switch type {
        case "ready":
            let vision = (json["vision_enabled"] as? Bool) ?? false
            let caps = Self.parseCapabilities(json)
            let version = (json["protocol_version"] as? Int) ?? 0
            let maxAge = (json["alert_max_age_ms"] as? Int).map { Int64($0) } ?? Self.staleAlertMs
            stateLock.lock()
            sessionReady = true
            visionEnabled = vision
            serverCapabilities = caps
            serverProtocolVersion = version
            configuredStaleAlertMs = max(maxAge, Self.staleAlertMs)
            // A working `ready` proves this connection is healthy end to end. Reset the backoff
            // counter here, not merely on the next successful frame, so a flapping link that
            // reconnects and gets a ready every time never accumulates a runaway backoff.
            reconnectAttempt = 0
            stateLock.unlock()
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                if vision {
                    self.delegate?.protocolClientDidConnect(self)
                } else {
                    self.delegate?.protocolClientVisionNotReady(self)
                    AlertManager.shared.speakStatus(
                        "Vision service is in bench mode. Assistance cannot start.",
                        force: true
                    )
                }
            }
        case "result":
            handleResult(json)
        case "quality":
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.delegate?.protocolClient(self, didReceiveQuality: json)
            }
        case "error":
            settleFrame()
            let code = json["code"] as? String
            if let code = code, Self.terminalErrorCodes.contains(code) {
                // The close frame that follows this message may carry an application close code
                // (4401/4403) that iOS's public WebSocket API cannot represent -- it collapses to
                // a generic "invalid" close code and the number is lost. This message body is the
                // reliable channel: act on it now rather than waiting for a close event that may
                // never distinguish itself from an ordinary transport drop.
                handleTerminalRejection(code: code)
                return
            }
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.delegate?.protocolClient(self, didReceiveError: json)
            }
        case "pong":
            break
        default:
            break
        }
    }

    private func handleResult(_ payload: [String: Any]) {
        cancelSettleTimeout()
        stateLock.lock()
        consecutiveSettleTimeouts = 0
        stateLock.unlock()

        let captureMono = Self.jsonInt64(payload["capture_mono_ms"])
        let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        let age = Self.resultAgeMs(captureMonoMs: captureMono, nowMs: nowMs)
        let priority = Self.jsonBool(payload["priority"])
        let hazard = payload["hazard"] as? [String: Any]
        let isUrgent = (hazard?["level"] as? String) == "urgent"

        stateLock.lock()
        let configured = configuredStaleAlertMs
        let ackSupported = serverCapabilities.contains(Self.capabilityResultAcknowledgement)
        let task = webSocketTask
        stateLock.unlock()

        let maxAge = Self.maxSpeakAgeMs(
            priority: priority,
            isUrgent: isUrgent,
            configuredStaleAlertMs: configured
        )
        let fresh = age <= maxAge

        if fresh {
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.delegate?.protocolClient(self, didReceiveResult: payload)
            }
        } else {
            AgentDebugLog.log(message: "result_stale age=\(age) max=\(maxAge) priority=\(priority)")
        }

        if ackSupported, let frameId = Self.jsonInt64(payload["frame_id"]), frameId >= 0 {
            acknowledgeResult(task: task, frameId: Int(frameId), fresh: fresh)
        }
        settleFrame()
    }

    private func acknowledgeResult(task: URLSessionWebSocketTask?, frameId: Int, fresh: Bool) {
        let body: [String: Any] = [
            "type": "result_ack",
            "frame_id": frameId,
            "fresh": fresh,
        ]
        guard JSONSerialization.isValidJSONObject(body),
              let data = try? JSONSerialization.data(withJSONObject: body),
              let text = String(data: data, encoding: .utf8) else { return }
        task?.send(.string(text)) { error in
            if error != nil {
                AgentDebugLog.log(message: "result_ack_not_sent frame=\(frameId)")
            }
        }
    }

    /// The authenticated device has been permanently rejected (revoked, or auth failed and the
    /// server said so explicitly). No reconnect: retrying against a revoked/rejected identity
    /// would just reproduce the same rejection every 1-60s forever.
    private func handleTerminalRejection(code: String) {
        stateLock.lock()
        if permanentFailure || closedByUser {
            stateLock.unlock()
            return
        }
        permanentFailure = true
        closedByUser = true
        receiveLoopActive = false
        let task = webSocketTask
        let session = urlSession
        stateLock.unlock()

        cancelSettleTimeout()
        cancelStaleInferenceWatchdog()
        cancelPing()
        settleFrame()
        clearNegotiatedState()
        task?.cancel(with: .policyViolation, reason: nil)
        session?.invalidateAndCancel()

        let message = code == "device_revoked"
            ? "Device revoked. Assistance stopped."
            : "Authentication failed. Re-provision required."
        let closeCode = code == "device_revoked" ? 4403 : 4401
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            AlertManager.shared.speakStatus(message, force: true)
            self.delegate?.protocolClient(self, didDisconnectWithCode: closeCode, reason: code)
        }
    }

    /// Capped exponential backoff with full jitter. Guards against being scheduled twice for the
    /// same drop (listen()'s `.failure` and `didCloseWith` can both fire) and against scheduling
    /// at all once the client is terminal.
    private func scheduleReconnect(origin: String) {
        stateLock.lock()
        guard !reconnectScheduled, !closedByUser, !permanentFailure,
              let url = reconnectURL, !authToken.isEmpty else {
            stateLock.unlock()
            return
        }
        reconnectScheduled = true
        reconnectAttempt += 1
        let attempt = reconnectAttempt
        let token = authToken
        stateLock.unlock()

        let delay = Self.reconnectDelaySeconds(attempt: attempt)
        AgentDebugLog.log(message: "reconnect_scheduled origin=\(origin) attempt=\(attempt) delaySec=\(delay)")
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self else { return }
            self.stateLock.lock()
            self.reconnectScheduled = false
            let shouldGo = !self.closedByUser && !self.permanentFailure
            self.stateLock.unlock()
            guard shouldGo else { return }
            self.openSocket(url: url, token: token, origin: origin)
        }
    }

    private func handleTransportDrop(code: Int, reason: String?, generation: Int) {
        stateLock.lock()
        guard generation == connectionGeneration, dropHandledGeneration != generation else {
            stateLock.unlock()
            return
        }
        dropHandledGeneration = generation
        receiveLoopActive = false
        stateLock.unlock()

        cancelPing()
        settleFrame()
        clearNegotiatedState()
        // 4401 / 4403: token rejected or device revoked — permanent; do not reconnect. In
        // practice this branch is usually pre-empted by handleTerminalRejection acting on the
        // "error" message body first (see handleMessage), since iOS's CloseCode enum cannot carry
        // these values through this delegate path at all -- but keep the check for a server that
        // sends the close before any message, or for a future close code Foundation does add
        // native support for.
        if code == 4401 || code == 4403 {
            handleTerminalRejection(code: code == 4403 ? "device_revoked" : "authentication_failed")
            return
        }
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.delegate?.protocolClient(self, didDisconnectWithCode: code, reason: reason)
        }
        guard !isTerminal() else { return }
        scheduleReconnect(origin: "transport_drop")
    }

    public func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        guard isCurrentTask(webSocketTask) else { return }
        AgentDebugLog.log(message: "transport_open")
    }

    public func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        guard isCurrentTask(webSocketTask) else { return }
        let reasonStr = reason.flatMap { String(data: $0, encoding: .utf8) }
        handleTransportDrop(code: closeCode.rawValue, reason: reasonStr, generation: currentGeneration())
    }

    /// Covers the HTTP-level rejection path: an auth failure the server detects BEFORE accepting
    /// the WebSocket handshake closes the connection at the HTTP-upgrade level (commonly surfaced
    /// as a 401/403 status on `task.response`), which never reaches `didCloseWith` at all --
    /// `listen()` would otherwise see this only as an anonymous `.failure` mapped to code 1006,
    /// indistinguishable from an ordinary network drop, and retry forever against a token that
    /// will never be accepted.
    public func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard isCurrentTask(task) else { return }
        if let http = task.response as? HTTPURLResponse, http.statusCode == 401 || http.statusCode == 403 {
            handleTerminalRejection(code: http.statusCode == 403 ? "device_revoked" : "authentication_failed")
        }
        // Any other completion (including a plain network error) is already handled by
        // listen()'s `.failure` case or by didCloseWith; nothing further to do here.
    }
}
