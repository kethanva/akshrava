//
//  AlertAudioFocus.swift
//  Akshrava iOS
//
//  Transient audio session focus management — mirrors Android AlertAudioFocus.kt.
//  On iOS, AVAudioSession handles ducking automatically via the .duckOthers option
//  set in AlertManager. This object encapsulates the shouldRequest policy gate so
//  tests can verify the logic without an AVAudioSession.
//  Guarded with #if os(iOS) for macOS SPM test compilation.
//

import Foundation

public enum AlertAudioFocus {
    /// Focus should only be requested when the engine is ready AND the session is open.
    /// An already-closed session must not re-acquire focus — mirroring F-09 policy.
    public static func shouldRequest(ready: Bool, closed: Bool) -> Bool {
        return ready && !closed
    }

    /// Checks whether the focus hold counter indicates an active focus hold.
    /// A count > 0 means at least one utterance is in progress.
    public static func isHolding(holdCount: Int) -> Bool {
        return holdCount > 0
    }

    /// Acquires a focus hold. Returns true only on the FIRST acquisition (i.e., when going
    /// from zero → one). Nested acquisitions (queue/flush) return false so the caller
    /// knows not to issue a redundant system focus request.
    public static func acquire(holdCount: inout Int) -> Bool {
        let previous = holdCount
        holdCount += 1
        return previous == 0
    }

    /// Releases a focus hold. Returns true only when the hold count reaches zero,
    /// indicating the caller should abandon system audio focus.
    public static func release(holdCount: inout Int) -> Bool {
        holdCount = max(0, holdCount - 1)
        return holdCount == 0
    }
}
