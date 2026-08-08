//
//  HapticFeedbackEngine.swift
//  Akshrava iOS
//
//  Haptic feedback for urgent/caution alerts — mirrors Android VIBRATE permission.
//  UIFeedbackGenerator is iOS-only; on macOS (CI/SPM swift test) this is a no-op.
//

import Foundation
#if canImport(UIKit)
import UIKit
#endif

public class HapticFeedbackEngine {
    #if canImport(UIKit)
    private let generator = UINotificationFeedbackGenerator()
    #endif

    public init() {}

    public func triggerUrgent() {
        #if canImport(UIKit)
        generator.notificationOccurred(.error)
        #endif
    }

    public func triggerCaution() {
        #if canImport(UIKit)
        generator.notificationOccurred(.warning)
        #endif
    }
}
