//
//  FrameEncoder.swift
//  Akshrava iOS
//
//  JPEG frame encoder for AVCaptureOutput sample buffers.
//  Single-pass CIContext.jpegRepresentation encode, guarded with #if os(iOS) for macOS SPM CI.
//

import Foundation
import CoreImage
import ImageIO
#if os(iOS)
import AVFoundation
#endif

public struct EncodedFrame {
    public let jpegData: Data
    public let width: Int
    public let height: Int
    public let captureMonoMs: Int64
}

public class FrameEncoder {
    /// Shared CIContext — reused across encode calls to avoid per-frame GPU allocation.
    private let context = CIContext()

    public init() {}

    #if os(iOS)
    public func encode(sampleBuffer: CMSampleBuffer, compressionQuality: CGFloat = 0.7) -> EncodedFrame? {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return nil }
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)

        // Single GPU pass straight to JPEG bytes. The previous path was three full-frame
        // conversions per capture (CIImage -> CGImage render+readback, CGImage -> UIImage wrapper
        // alloc, UIImage -> JPEG re-encode) on every frame, at up to several fps on a donated
        // phone -- avoidable heat and battery on exactly the device class this app targets.
        guard let jpegData = context.jpegRepresentation(
            of: ciImage,
            colorSpace: CGColorSpaceCreateDeviceRGB(),
            options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: compressionQuality]
        ) else { return nil }

        // AppConfig.maxFrameJpegSizeBytes existed as a declared constant with nothing enforcing
        // it. The backend already rejects an oversized frame (`unsupported_frame_size`), but
        // dropping it here saves the upload entirely rather than paying for it and being told no.
        guard jpegData.count <= AppConfig.shared.maxFrameJpegSizeBytes else {
            AgentDebugLog.log(message: "frame_encode_oversize bytes=\(jpegData.count)")
            return nil
        }

        let monoMs = Int64(ProcessInfo.processInfo.systemUptime * 1000)
        return EncodedFrame(
            jpegData: jpegData,
            width: Int(ciImage.extent.width),
            height: Int(ciImage.extent.height),
            captureMonoMs: monoMs
        )
    }
    #endif
}
