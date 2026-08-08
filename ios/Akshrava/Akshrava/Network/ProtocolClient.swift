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

public final class ProtocolClient: NSObject, URLSessionWebSocketDelegate {
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

    private var webSocketTask: URLSessionWebSocketTask?
    private var urlSession: URLSession?
    private var authToken: String = ""
    private var reconnectURL: URL?
    private var closedByUser = false
    private var connectionGeneration = 0

    private let stateLock = NSLock()
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

    /// True after user stop or permanent auth failure — Start must rebuild, reconnect will not.
    public func isTerminal() -> Bool { closedByUser || permanentFailure }

    /// Connect requires a non-blank JWT. Empty token fails closed — production WSS rejects anonymous sockets.
    public func connect(url: URL, authToken: String) {
        let trimmed = authToken.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            AgentDebugLog.log(message: "connect_rejected reason=missing_token")
            DispatchQueue.main.async {
                self.delegate?.protocolClient(self, didReceiveError: [
                    "type": "error",
                    "code": "provisioning_required",
                    "detail": "Provisioning required",
                ])
            }
            return
        }
        closedByUser = false
        permanentFailure = false
        self.authToken = trimmed
        self.reconnectURL = url
        openSocket(url: url, token: trimmed, origin: "initial")
    }

    public func disconnect() {
        closedByUser = true
        cancelSettleTimeout()
        cancelStaleInferenceWatchdog()
        cancelPing()
        settleFrame()
        clearNegotiatedState()
        webSocketTask?.cancel(with: .normalClosure, reason: Data("user stopped".utf8))
        webSocketTask = nil
        urlSession?.invalidateAndCancel()
        urlSession = nil
        receiveLoopActive = false
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
        guard webSocketTask != nil else { return failSendFrame(priority: priority, reason: "socket_missing") }
        guard canStream() else {
            delegate?.protocolClientVisionNotReady(self)
            return failSendFrame(priority: priority, reason: "vision_not_ready")
        }

        stateLock.lock()
        if inFlight {
            stateLock.unlock()
            return failSendFrame(priority: priority, reason: "frame_in_flight")
        }
        inFlight = true
        let fullPose = serverCapabilities.contains(Self.capabilityPoseCdegFullRange)
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

        let task = webSocketTask
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
                    return
                }
                let look = priority || mode == "priority"
                self.frameSentAtMonoMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
                self.scheduleSettleTimeout(isLook: look)
                self.scheduleStaleInferenceWatchdog()
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
        connectionGeneration += 1
        let generation = connectionGeneration
        clearNegotiatedState()

        webSocketTask?.cancel(with: .goingAway, reason: nil)
        urlSession?.invalidateAndCancel()

        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        urlSession = URLSession(configuration: configuration, delegate: self, delegateQueue: OperationQueue())

        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        webSocketTask = urlSession?.webSocketTask(with: request)
        receiveLoopActive = true
        webSocketTask?.resume()
        listen(generation: generation)
        schedulePing(generation: generation)
        AgentDebugLog.log(message: "connect_attempt origin=\(origin)")
    }

    private func schedulePing(generation: Int) {
        cancelPing()
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, generation == self.connectionGeneration, !self.closedByUser else { return }
            let body: [String: Any] = ["type": "ping"]
            if let data = try? JSONSerialization.data(withJSONObject: body),
               let text = String(data: data, encoding: .utf8) {
                self.webSocketTask?.send(.string(text)) { _ in }
            }
            self.schedulePing(generation: generation)
        }
        pingWorkItem = work
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.appPingIntervalMs)),
            execute: work
        )
    }

    private func cancelPing() {
        pingWorkItem?.cancel()
        pingWorkItem = nil
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
        let generation = connectionGeneration
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, generation == self.connectionGeneration, !self.closedByUser else { return }
            if isLook {
                AlertManager.shared.speakStatus("Look failed. Try again.", force: true)
            }
            self.settleFrame()
            self.stateLock.lock()
            self.consecutiveSettleTimeouts += 1
            let shouldReconnect = self.consecutiveSettleTimeouts >= Self.settleTimeoutsBeforeReconnect
            if shouldReconnect { self.consecutiveSettleTimeouts = 0 }
            self.stateLock.unlock()
            if shouldReconnect, let url = self.reconnectURL, !self.authToken.isEmpty {
                AgentDebugLog.log(message: "reconnect reason=repeated_settle_timeout")
                self.openSocket(url: url, token: self.authToken, origin: "settle_timeout")
            }
        }
        settleWorkItem = work
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.frameSettleTimeoutMs)),
            execute: work
        )
    }

    private func cancelSettleTimeout() {
        settleWorkItem?.cancel()
        settleWorkItem = nil
    }

    private func scheduleStaleInferenceWatchdog() {
        cancelStaleInferenceWatchdog()
        staleInferenceTicks = 0
        let generation = connectionGeneration
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, generation == self.connectionGeneration, !self.closedByUser else { return }
            self.stateLock.lock()
            let flying = self.inFlight
            let sentAt = self.frameSentAtMonoMs
            let ticks = self.staleInferenceTicks
            self.stateLock.unlock()
            let now = Int64(ProcessInfo.processInfo.systemUptime * 1000)
            let age = sentAt > 0 ? now - sentAt : 0
            if Self.shouldTickStaleInference(inFlight: flying, frameAgeMs: age, ticksAlready: ticks) {
                self.stateLock.lock()
                self.staleInferenceTicks += 1
                self.stateLock.unlock()
                ConnectionEarcons.playDropped() // bounded tick — not an unbounded loop
                AgentDebugLog.log(message: "stale_inference_tick age=\(age) ticks=\(ticks + 1)")
            }
            if flying, ticks + 1 < Self.staleInferenceMaxTicks {
                self.scheduleStaleInferenceWatchdogContinue(generation: generation)
            }
        }
        staleInferenceWorkItem = work
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.staleInferenceTickAfterMs)),
            execute: work
        )
    }

    private func scheduleStaleInferenceWatchdogContinue(generation: Int) {
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, generation == self.connectionGeneration, !self.closedByUser else { return }
            self.stateLock.lock()
            let flying = self.inFlight
            let sentAt = self.frameSentAtMonoMs
            let ticks = self.staleInferenceTicks
            self.stateLock.unlock()
            let now = Int64(ProcessInfo.processInfo.systemUptime * 1000)
            let age = sentAt > 0 ? now - sentAt : 0
            guard Self.shouldTickStaleInference(inFlight: flying, frameAgeMs: age, ticksAlready: ticks) else { return }
            self.stateLock.lock()
            self.staleInferenceTicks += 1
            let next = self.staleInferenceTicks
            self.stateLock.unlock()
            ConnectionEarcons.playDropped()
            if flying, next < Self.staleInferenceMaxTicks {
                self.scheduleStaleInferenceWatchdogContinue(generation: generation)
            }
        }
        staleInferenceWorkItem = work
        DispatchQueue.global().asyncAfter(
            deadline: .now() + .milliseconds(Int(Self.staleInferenceTickPeriodMs)),
            execute: work
        )
    }

    private func cancelStaleInferenceWatchdog() {
        staleInferenceWorkItem?.cancel()
        staleInferenceWorkItem = nil
        staleInferenceTicks = 0
    }

    private func settleFrame() {
        cancelSettleTimeout()
        cancelStaleInferenceWatchdog()
        stateLock.lock()
        let wasInFlight = inFlight
        inFlight = false
        stateLock.unlock()
        if wasInFlight {
            DispatchQueue.main.async {
                self.delegate?.protocolClientDidSettleFrame(self)
            }
        }
    }

    private func listen(generation: Int) {
        guard receiveLoopActive, generation == connectionGeneration else { return }
        webSocketTask?.receive { [weak self] result in
            guard let self = self, generation == self.connectionGeneration else { return }
            switch result {
            case .failure(let error):
                self.handleTransportDrop(code: 1006, reason: error.localizedDescription)
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
                if self.receiveLoopActive {
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
            DispatchQueue.main.async {
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
            stateLock.unlock()
            DispatchQueue.main.async {
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
            DispatchQueue.main.async {
                self.delegate?.protocolClient(self, didReceiveQuality: json)
            }
        case "error":
            settleFrame()
            DispatchQueue.main.async {
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
        consecutiveSettleTimeouts = 0

        let captureMono = Self.jsonInt64(payload["capture_mono_ms"])
        let nowMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        let age = Self.resultAgeMs(captureMonoMs: captureMono, nowMs: nowMs)
        let priority = Self.jsonBool(payload["priority"])
        let hazard = payload["hazard"] as? [String: Any]
        let isUrgent = (hazard?["level"] as? String) == "urgent"

        stateLock.lock()
        let configured = configuredStaleAlertMs
        let ackSupported = serverCapabilities.contains(Self.capabilityResultAcknowledgement)
        stateLock.unlock()

        let maxAge = Self.maxSpeakAgeMs(
            priority: priority,
            isUrgent: isUrgent,
            configuredStaleAlertMs: configured
        )
        let fresh = age <= maxAge

        if fresh {
            DispatchQueue.main.async {
                self.delegate?.protocolClient(self, didReceiveResult: payload)
            }
        } else {
            AgentDebugLog.log(message: "result_stale age=\(age) max=\(maxAge) priority=\(priority)")
        }

        if ackSupported, let frameId = Self.jsonInt64(payload["frame_id"]), frameId >= 0 {
            acknowledgeResult(frameId: Int(frameId), fresh: fresh)
        }
        settleFrame()
    }

    private func acknowledgeResult(frameId: Int, fresh: Bool) {
        let body: [String: Any] = [
            "type": "result_ack",
            "frame_id": frameId,
            "fresh": fresh,
        ]
        guard JSONSerialization.isValidJSONObject(body),
              let data = try? JSONSerialization.data(withJSONObject: body),
              let text = String(data: data, encoding: .utf8) else { return }
        webSocketTask?.send(.string(text)) { error in
            if error != nil {
                AgentDebugLog.log(message: "result_ack_not_sent frame=\(frameId)")
            }
        }
    }

    private func handleTransportDrop(code: Int, reason: String?) {
        receiveLoopActive = false
        cancelPing()
        settleFrame()
        clearNegotiatedState()
        // 4401 / 4403: token rejected or device revoked — permanent; do not reconnect.
        if code == 4401 || code == 4403 {
            permanentFailure = true
            closedByUser = true
            let message = code == 4403
                ? "Device revoked. Assistance stopped."
                : "Authentication failed. Re-provision required."
            DispatchQueue.main.async {
                AlertManager.shared.speakStatus(message, force: true)
                self.delegate?.protocolClient(self, didDisconnectWithCode: code, reason: reason)
            }
            return
        }
        DispatchQueue.main.async {
            self.delegate?.protocolClient(self, didDisconnectWithCode: code, reason: reason)
        }
        guard !isTerminal(), let url = reconnectURL, !authToken.isEmpty else { return }
        DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) { [weak self] in
            guard let self = self, !self.isTerminal() else { return }
            self.openSocket(url: url, token: self.authToken, origin: "transport_drop")
        }
    }

    public func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        AgentDebugLog.log(message: "transport_open")
    }

    public func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        let reasonStr = reason.flatMap { String(data: $0, encoding: .utf8) }
        handleTransportDrop(code: Int(closeCode.rawValue), reason: reasonStr)
    }
}
