//
//  FrameGate.swift
//  Akshrava iOS
//
//  Frame quality gate. Occlusion may drop a black frame. Glare and blur never drop —
//  they only drive a bounded announce after consecutive evidence + cooldown.
//

import Foundation
import CoreImage
#if os(iOS)
import AVFoundation
#else
import CoreMedia
#endif

public enum FrameGateResult: Equatable {
    case pass
    case occluded
    case glared
}

public final class FrameGate {
    public static let blurFramesBeforeAnnounce = 5
    public static let blurAnnounceCooldownMs: Int64 = 60_000

    /// Shared CIContext — creating one per frame is very expensive (GPU allocation).
    /// Reusing it is safe: CIContext is thread-safe for concurrent reads.
    private static let ciContext = CIContext()

    /// Whether the wipe-lens prompt may speak now (F-72). Blur never drops a frame.
    public static func shouldAnnounceBlur(
        nowMs: Int64,
        consecutiveBlurredFrames: Int,
        lastAnnounceMs: Int64?
    ) -> Bool {
        if consecutiveBlurredFrames < blurFramesBeforeAnnounce { return false }
        guard let last = lastAnnounceMs else { return true }
        return nowMs - last >= blurAnnounceCooldownMs
    }

    private var consecutiveBlurredFrames = 0
    private var lastBlurAnnounceMs: Int64?

    public init() {}

    public func resetBlurTracking() {
        consecutiveBlurredFrames = 0
        lastBlurAnnounceMs = nil
    }

    /// Drop decision for this frame. Blur is tracked separately via [noteBlurred]/shouldAnnounceBlurPrompt].
    public func evaluate(sampleBuffer: CMSampleBuffer) -> FrameGateResult {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            return .occluded
        }
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        if isOccluded(ciImage: ciImage) { return .occluded }
        if isGlared(ciImage: ciImage) { return .glared }
        return .pass
    }

    public func noteBlurred(_ blurred: Bool) {
        if blurred {
            consecutiveBlurredFrames += 1
        } else {
            consecutiveBlurredFrames = 0
        }
    }

    public func shouldAnnounceBlurPrompt(nowMs: Int64) -> Bool {
        Self.shouldAnnounceBlur(
            nowMs: nowMs,
            consecutiveBlurredFrames: consecutiveBlurredFrames,
            lastAnnounceMs: lastBlurAnnounceMs
        )
    }

    public func markBlurAnnounced(nowMs: Int64) {
        lastBlurAnnounceMs = nowMs
        consecutiveBlurredFrames = 0
    }

    /// Edge-energy proxy. Prefer false negatives over false wipe-lens prompts (F-72).
    public func isBlurred(sampleBuffer: CMSampleBuffer) -> Bool {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return false }
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let extent = ciImage.extent
        guard extent.width > 8, extent.height > 8 else { return false }
        guard let edges = CIFilter(name: "CIEdges", parameters: [
            kCIInputImageKey: ciImage,
            kCIInputIntensityKey: 1.0,
        ])?.outputImage,
              let avg = CIFilter(name: "CIAreaAverage", parameters: [
                kCIInputImageKey: edges,
                kCIInputExtentKey: CIVector(cgRect: extent),
              ])?.outputImage else {
            return false
        }
        var bitmap = [UInt8](repeating: 0, count: 4)
        Self.ciContext.render(
            avg,
            toBitmap: &bitmap,
            rowBytes: 4,
            bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
            format: .RGBA8,
            colorSpace: nil
        )
        // Low edge energy → blur. Threshold kept strict to avoid dusk false positives.
        return bitmap[0] < 6
    }

    private func isOccluded(ciImage: CIImage) -> Bool {
        guard let luminance = meanLuma(ciImage: ciImage) else { return false }
        return luminance < 12.0
    }

    private func isGlared(ciImage: CIImage) -> Bool {
        guard let filter = CIFilter(name: "CIAreaAverage", parameters: [
            kCIInputImageKey: ciImage,
            kCIInputExtentKey: CIVector(cgRect: ciImage.extent),
        ]), let outputImage = filter.outputImage else { return false }

        var bitmap = [UInt8](repeating: 0, count: 4)
        Self.ciContext.render(
            outputImage,
            toBitmap: &bitmap,
            rowBytes: 4,
            bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
            format: .RGBA8,
            colorSpace: nil
        )
        return bitmap[0] > 250 && bitmap[1] > 250 && bitmap[2] > 250
    }

    private func meanLuma(ciImage: CIImage) -> Double? {
        guard let filter = CIFilter(name: "CIAreaAverage", parameters: [
            kCIInputImageKey: ciImage,
            kCIInputExtentKey: CIVector(cgRect: ciImage.extent),
        ]), let outputImage = filter.outputImage else { return nil }

        var bitmap = [UInt8](repeating: 0, count: 4)
        Self.ciContext.render(
            outputImage,
            toBitmap: &bitmap,
            rowBytes: 4,
            bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
            format: .RGBA8,
            colorSpace: nil
        )
        return 0.299 * Double(bitmap[0]) + 0.587 * Double(bitmap[1]) + 0.114 * Double(bitmap[2])
    }
}
