//
//  ScreenKeepAlive.swift
//  Akshrava iOS
//
//  Keeps the display awake so the camera keeps delivering frames — mirrors Android ScreenKeepAlive.kt.
//
//  On iOS, UIApplication.shared.isIdleTimerDisabled = true prevents screen sleep.
//  This is the idiomatic iOS equivalent of Android's FLAG_KEEP_SCREEN_ON overlay.
//  Guarded with #if os(iOS) for macOS SPM compilation.
//

import Foundation

#if os(iOS)
import UIKit

public class ScreenKeepAlive {
    public enum Mode { case none, idleTimerDisabled }

    public private(set) var mode: Mode = .none

    public init() {}

    /// Disables the idle timer, preventing screen sleep during an active session.
    /// Returns true if the display is being held awake.
    @discardableResult
    public func start() -> Bool {
        guard !isHoldingScreenOn() else { return true }
        UIApplication.shared.isIdleTimerDisabled = true
        mode = .idleTimerDisabled
        return true
    }

    /// Re-enables the idle timer and allows the display to sleep normally.
    public func stop() {
        UIApplication.shared.isIdleTimerDisabled = false
        mode = .none
    }

    /// True when the idle timer is currently disabled.
    public func isHoldingScreenOn() -> Bool {
        return UIApplication.shared.isIdleTimerDisabled
    }

    /// No-op on iOS (idle timer disabling does not expire).
    public func renew() {}
}

#else

/// macOS stub for SPM compilation.
public class ScreenKeepAlive {
    public enum Mode { case none, idleTimerDisabled }
    public private(set) var mode: Mode = .none
    public init() {}
    @discardableResult public func start() -> Bool { return false }
    public func stop() {}
    public func isHoldingScreenOn() -> Bool { return false }
    public func renew() {}
}

#endif
