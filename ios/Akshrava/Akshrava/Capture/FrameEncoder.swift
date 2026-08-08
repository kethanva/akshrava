//
//  FrameEncoder.swift
//  Akshrava iOS
//
//  JPEG frame encoder for AVCaptureOutput sample buffers.
//  Uses CoreImage (macOS + iOS) for pixel conversion and UIImage (iOS-only) for JPEG output.
//  UIImage is guarded with #if os(iOS) for SPM macOS CI.
//

import Foundation
import CoreImage
#if os(iOS)
import AVFoundation
import UIKit
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

        guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else { return nil }
        let uiImage = UIImage(cgImage: cgImage)

        guard let jpegData = uiImage.jpegData(compressionQuality: compressionQuality) else { return nil }

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
