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
    func captureController(_ controller: CaptureController, didEncounterStall error: Error)
}

public class CaptureController: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    public weak var delegate: CaptureControllerDelegate?

    private let captureSession = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let sessionQueue = DispatchQueue(label: "org.akshrava.ios.camera.session")
    private let videoQueue = DispatchQueue(label: "org.akshrava.ios.camera.video")

    private var isRunning = false
    private var lastFrameTime: Date = Date()
    private var stallCheckTimer: Timer?

    public override init() {
        super.init()
    }

    public func startCapture() {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            if self.isRunning { return }
            self.setupCamera()
            self.captureSession.startRunning()
            self.isRunning = true
            DispatchQueue.main.async {
                self.startStallCheckTimer()
            }
        }
    }

    public func stopCapture() {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            if !self.isRunning { return }
            self.captureSession.stopRunning()
            self.isRunning = false
            DispatchQueue.main.async {
                self.stallCheckTimer?.invalidate()
                self.stallCheckTimer = nil
            }
        }
    }

    private func setupCamera() {
        captureSession.beginConfiguration()
        captureSession.sessionPreset = .vga640x480

        guard let backCamera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: backCamera) else {
            captureSession.commitConfiguration()
            return
        }

        if captureSession.canAddInput(input) {
            captureSession.addInput(input)
        }

        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)]
        videoOutput.setSampleBufferDelegate(self, queue: videoQueue)

        if captureSession.canAddOutput(videoOutput) {
            captureSession.addOutput(videoOutput)
        }

        captureSession.commitConfiguration()
    }

    public func captureOutput(_ output: AVCaptureOutput,
                              didOutput sampleBuffer: CMSampleBuffer,
                              from connection: AVCaptureConnection) {
        lastFrameTime = Date()
        delegate?.captureController(self, didOutputFrame: sampleBuffer)
    }

    private func startStallCheckTimer() {
        stallCheckTimer?.invalidate()
        stallCheckTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            guard let self = self, self.isRunning else { return }
            if Date().timeIntervalSince(self.lastFrameTime) > 4.0 {
                let err = NSError(domain: "org.akshrava.camera", code: 500,
                                  userInfo: [NSLocalizedDescriptionKey: "Camera output stall detected"])
                self.delegate?.captureController(self, didEncounterStall: err)
                self.restartCamera()
            }
        }
    }

    private func restartCamera() {
        sessionQueue.async { [weak self] in
            guard let self = self else { return }
            if self.captureSession.isRunning {
                self.captureSession.stopRunning()
            }
            self.captureSession.startRunning()
        }
    }
}
#endif
