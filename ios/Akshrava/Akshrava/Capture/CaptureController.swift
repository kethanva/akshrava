//
//  CaptureController.swift
//  Akshrava iOS
//
//  Camera capture pipeline using AVFoundation — iOS only.
//  Guarded with #if os(iOS) so SPM compiles on macOS CI.
//

import Foundation
#if os(iOS)
import AVFoundation
import UIKit

public protocol CaptureControllerDelegate: AnyObject {
    func captureController(_ controller: CaptureController, didOutputFrame sampleBuffer: CMSampleBuffer)
    /// A transient stall was detected and CaptureController is already recovering it (bounded
    /// retries). Announce-only: the delegate must not itself restart the session, or the two
    /// owners race on the same AVCaptureSession.
    func captureController(_ controller: CaptureController, didEncounterStall error: Error)
    /// Recovery is exhausted (permission denied, or repeated stalls past the retry bound).
    /// Capture has stopped. This is the terminal signal -- speak once, do not loop.
    func captureController(_ controller: CaptureController, didBecomeUnavailable error: Error)
    /// The system paused capture out from under the app (another app took the camera, the app
    /// entered the background, or multitasking camera access was revoked). Silence here would be
    /// indistinguishable from a dead app to a user who cannot see the screen.
    func captureControllerWasInterrupted(_ controller: CaptureController)
    /// The interruption cleared and capture resumed on its own.
    func captureControllerDidResumeFromInterruption(_ controller: CaptureController)
}

public extension CaptureControllerDelegate {
    func captureControllerWasInterrupted(_ controller: CaptureController) {}
    func captureControllerDidResumeFromInterruption(_ controller: CaptureController) {}
}

public class CaptureController: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    public weak var delegate: CaptureControllerDelegate?

    /// A stalled camera is retried this many times before capture is stopped and the delegate is
    /// told recovery is exhausted. Without a bound, a permanently dead camera HAL fires the stall
    /// timer every 3s forever, and the spoken "recovering" announcement becomes a permanent loop
    /// in the user's only audio channel.
    public static let maxStallRecoveries = 3

    private let captureSession = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let sessionQueue = DispatchQueue(label: "org.akshrava.ios.camera.session")
    private let videoQueue = DispatchQueue(label: "org.akshrava.ios.camera.video")

    // Three queues genuinely touch this state and none of them is the same one: frames arrive on
    // videoQueue, session start/stop runs on sessionQueue, and the stall timer fires on main.
    // `lastFrameTime` in particular is written per-frame on videoQueue and read by the stall check
    // on main -- an unsynchronised read there either restarts a healthy camera (a multi-second
    // detection blackout) or fails to restart a dead one. `stallCheckTimer` stays main-confined
    // (Timer scheduling requires it) and is deliberately not covered by this lock.
    private let stateLock = NSLock()
    private var _isRunning = false
    private var _desiredRunning = false
    // Read/written only on sessionQueue. AVCaptureSession retains its inputs/outputs across
    // stopRunning(); adding them again on every Start makes canAddInput fail on the second
    // session, so configuration is a one-time operation for this controller instance.
    private var isConfigured = false
    private var _lastFrameTime: Date = Date()
    private var _stallRecoveries = 0
    private var stallCheckTimer: Timer?

    private var isRunning: Bool {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _isRunning }
        set { stateLock.lock(); _isRunning = newValue; stateLock.unlock() }
    }

    private var desiredRunning: Bool {
        get { stateLock.lock(); defer { stateLock.unlock() }; return _desiredRunning }
        set { stateLock.lock(); _desiredRunning = newValue; stateLock.unlock() }
    }

    public override init() {
        super.init()
        let center = NotificationCenter.default
        center.addObserver(
            self, selector: #selector(sessionWasInterrupted(_:)),
            name: .AVCaptureSessionWasInterrupted, object: captureSession
        )
        center.addObserver(
            self, selector: #selector(sessionInterruptionEnded(_:)),
            name: .AVCaptureSessionInterruptionEnded, object: captureSession
        )
        center.addObserver(
            self, selector: #selector(sessionRuntimeError(_:)),
            name: .AVCaptureSessionRuntimeError, object: captureSession
        )
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    @objc private func sessionWasInterrupted(_ note: Notification) {
        AgentDebugLog.log(message: "capture_session_interrupted")
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.delegate?.captureControllerWasInterrupted(self)
        }
    }

    @objc private func sessionInterruptionEnded(_ note: Notification) {
        AgentDebugLog.log(message: "capture_session_interruption_ended")
        sessionQueue.async { [weak self] in
            guard let self = self, self.desiredRunning, self.isRunning else { return }
            if !self.captureSession.isRunning {
                self.captureSession.startRunning()
            }
            guard self.desiredRunning, self.captureSession.isRunning else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self = self else { return }
                self.delegate?.captureControllerDidResumeFromInterruption(self)
            }
        }
    }

    @objc private func sessionRuntimeError(_ note: Notification) {
        AgentDebugLog.error(event: "capture_session_runtime_error")
        DispatchQueue.main.async { [weak self] in
            self?.checkForStall(force: true)
        }
    }

    /// Checks/requests camera authorization before touching AVCaptureSession. A denied or
    /// restricted camera must fail closed and tell the delegate, never run an input-less session
    /// that looks connected but will never deliver a frame.
    public func startCapture() {
        desiredRunning = true
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            beginSession()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                guard let self = self else { return }
                guard self.desiredRunning else { return }
                if granted {
                    self.beginSession()
                } else {
                    self.failClosed(reason: "camera_permission_denied")
                }
            }
        case .denied, .restricted:
            failClosed(reason: "camera_permission_denied")
        @unknown default:
            failClosed(reason: "camera_permission_unknown")
        }
    }

    public func stopCapture() {
        // Publish intent synchronously before the queued stop. A permission callback or queued
        // beginSession that arrives after Stop must see this and decline to start the camera.
        desiredRunning = false
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            if self.captureSession.isRunning {
                self.captureSession.stopRunning()
            }
            self.isRunning = false
            DispatchQueue.main.async {
                self.stallCheckTimer?.invalidate()
                self.stallCheckTimer = nil
            }
        }
    }

    private func beginSession() {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            guard self.desiredRunning, !self.isRunning else { return }
            guard self.isConfigured || self.setupCamera() else {
                self.failClosed(reason: "camera_unavailable")
                return
            }
            guard self.desiredRunning else { return }
            self.captureSession.startRunning()
            // Baseline the stall clock at the moment frames become expected, not on the first
            // frame: a configuration that is accepted and then never delivers is exactly the
            // failure the stall detector exists to catch, and starting the clock later would
            // arm nothing at all for it.
            self.stateLock.lock()
            self._lastFrameTime = Date()
            self._stallRecoveries = 0
            self._isRunning = true
            self.stateLock.unlock()
            DispatchQueue.main.async {
                self.startStallCheckTimer()
            }
        }
    }

    /// Always delivers on main. Reached from `AVCaptureDevice.requestAccess`'s completion (a queue
    /// Apple does not specify) and from sessionQueue, while the delegate ultimately drives UIKit
    /// (ScreenKeepAlive) and speech — neither is safe to touch off the main thread.
    private func failClosed(reason: String) {
        AgentDebugLog.error(event: "capture_unavailable", detail: reason)
        stateLock.lock()
        _desiredRunning = false
        _isRunning = false
        stateLock.unlock()
        let error = NSError(
            domain: "org.akshrava.camera",
            code: 403,
            userInfo: [NSLocalizedDescriptionKey: reason]
        )
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            self.delegate?.captureController(self, didBecomeUnavailable: error)
        }
    }

    /// Returns false when the camera input/output could not be attached -- setupCamera used to
    /// silently commit an empty configuration and let the caller believe capture had started.
    @discardableResult
    private func setupCamera() -> Bool {
        captureSession.beginConfiguration()
        captureSession.sessionPreset = .vga640x480

        // Recover cleanly from any earlier partial configuration (for example input succeeded
        // but output attachment failed). This runs only while sessionQueue owns configuration.
        for input in captureSession.inputs { captureSession.removeInput(input) }
        for output in captureSession.outputs { captureSession.removeOutput(output) }

        guard let backCamera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: backCamera),
              captureSession.canAddInput(input) else {
            captureSession.commitConfiguration()
            return false
        }
        captureSession.addInput(input)

        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)]
        videoOutput.setSampleBufferDelegate(self, queue: videoQueue)

        guard captureSession.canAddOutput(videoOutput) else {
            captureSession.commitConfiguration()
            return false
        }
        captureSession.addOutput(videoOutput)

        captureSession.commitConfiguration()
        isConfigured = true
        return true
    }

    public func captureOutput(_ output: AVCaptureOutput,
                              didOutput sampleBuffer: CMSampleBuffer,
                              from connection: AVCaptureConnection) {
        // A delivered frame both re-arms the stall window and clears the recovery budget, in one
        // transaction. These were two separate hops on two different queues, which let a restart
        // in flight race a frame arrival and lose the reset.
        stateLock.lock()
        _lastFrameTime = Date()
        _stallRecoveries = 0
        stateLock.unlock()
        delegate?.captureController(self, didOutputFrame: sampleBuffer)
    }

    private func startStallCheckTimer() {
        stallCheckTimer?.invalidate()
        stallCheckTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.checkForStall(force: false)
        }
    }

    /// What the stall check decided. Computing this under one lock acquisition keeps the
    /// "is it stalled / have we exhausted retries / consume one retry" decision atomic against a
    /// frame arriving on videoQueue mid-decision, which could otherwise both reset the budget and
    /// have its reset immediately overwritten.
    private enum StallDecision { case healthy, recover, exhausted }

    private func decideStall(force: Bool) -> StallDecision {
        stateLock.lock()
        defer { stateLock.unlock() }
        guard _desiredRunning, _isRunning,
              force || Date().timeIntervalSince(_lastFrameTime) > 4.0 else {
            return .healthy
        }
        // Re-arm the window immediately: this is the single owner of restart decisions, and
        // resetting the clock here (rather than only on a delivered frame) means a camera that
        // never recovers is retried on a fixed cadence instead of firing on every tick while a
        // restart is still in flight.
        _lastFrameTime = Date()
        guard _stallRecoveries < Self.maxStallRecoveries else { return .exhausted }
        _stallRecoveries += 1
        return .recover
    }

    private func checkForStall(force: Bool) {
        switch decideStall(force: force) {
        case .healthy:
            return
        case .exhausted:
            let error = NSError(
                domain: "org.akshrava.camera",
                code: 500,
                userInfo: [NSLocalizedDescriptionKey: "camera_stall_unrecoverable"]
            )
            stopCapture()
            delegate?.captureController(self, didBecomeUnavailable: error)
        case .recover:
            let error = NSError(
                domain: "org.akshrava.camera",
                code: 500,
                userInfo: [NSLocalizedDescriptionKey: "Camera output stall detected"]
            )
            delegate?.captureController(self, didEncounterStall: error)
            restartCamera()
        }
    }

    private func restartCamera() {
        sessionQueue.async { [weak self] in
            guard let self = self, self.desiredRunning, self.isRunning else { return }
            if self.captureSession.isRunning {
                self.captureSession.stopRunning()
            }
            guard self.desiredRunning else { return }
            self.captureSession.startRunning()
        }
    }
}
#endif
