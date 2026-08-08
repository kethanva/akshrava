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

    /// `UIApplication.shared` is main-thread-only. Every accessor here hops to main rather than
    /// trusting its callers: startSession()/stopSession() are legitimately reached from the
    /// BGTaskScheduler watchdog handler (an arbitrary background queue) and from
    /// AVCaptureDevice.requestAccess's completion (a queue Apple explicitly does not specify), so
    /// a caller-side guarantee does not exist to rely on. Touching UIApplication off-main is
    /// undefined behaviour that surfaces as a main-thread-checker crash on a real device.
    private func onMain(_ work: @escaping () -> Void) {
        if Thread.isMainThread {
            work()
        } else {
            DispatchQueue.main.async(execute: work)
        }
    }

    /// Disables the idle timer, preventing screen sleep during an active session.
    /// Returns true if the display is being held awake.
    @discardableResult
    public func start() -> Bool {
        mode = .idleTimerDisabled
        onMain {
            guard !UIApplication.shared.isIdleTimerDisabled else { return }
            UIApplication.shared.isIdleTimerDisabled = true
        }
        return true
    }

    /// Re-enables the idle timer and allows the display to sleep normally.
    public func stop() {
        mode = .none
        onMain { UIApplication.shared.isIdleTimerDisabled = false }
    }

    /// True when the idle timer is currently disabled.
    /// Reports this object's intent; reading `UIApplication.shared` here would itself be an
    /// off-main access from a background caller, and the two cannot disagree in practice because
    /// this class is the only writer during a session.
    public func isHoldingScreenOn() -> Bool {
        return mode == .idleTimerDisabled
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
