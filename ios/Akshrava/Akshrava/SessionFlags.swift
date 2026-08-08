//
//  SessionFlags.swift
//  Akshrava iOS
//
//  iOS equivalent of Android's SessionFlags.kt.
//  Tracks whether a session is active and records heartbeat timestamps so the
//  BGTaskScheduler watchdog can distinguish an OEM kill from an intentional stop.
//

import Foundation

public enum SessionFlags {
    private static let prefsKey = "org.akshrava.ios.prefs"
    private static let activeKey = "session_active"
    private static let heartbeatKey = "heartbeat_ms"

    /// A session whose heartbeat is older than 3 minutes while still marked active was killed.
    public static let staleAfterMs: Int64 = 3 * 60_000

    private static var prefs: UserDefaults {
        UserDefaults(suiteName: prefsKey) ?? .standard
    }

    public static func setActive(_ active: Bool) {
        let now = currentWallClockMs()
        prefs.set(active, forKey: activeKey)
        prefs.set(now, forKey: heartbeatKey)
        // Re-baseline the heartbeat throttle: a Start immediately after a Stop must not have its
        // first few heartbeats suppressed by the previous session's write clock.
        heartbeatLock.lock()
        lastHeartbeatWriteMs = now
        heartbeatLock.unlock()
    }

    /// Throttled: called on EVERY delivered camera frame, but the watchdog only cares about
    /// 3-minute staleness, so a write per frame was pure disk churn on a donated phone for no
    /// added signal. `lastWrittenMs` is guarded because frames arrive on the capture queue while
    /// setActive() is called from main.
    public static func heartbeat() {
        let now = currentWallClockMs()
        heartbeatLock.lock()
        let due = now - lastHeartbeatWriteMs >= heartbeatWriteIntervalMs
        if due { lastHeartbeatWriteMs = now }
        heartbeatLock.unlock()
        guard due else { return }
        prefs.set(now, forKey: heartbeatKey)
    }

    /// Well inside staleAfterMs so a live session can never be misread as killed.
    private static let heartbeatWriteIntervalMs: Int64 = 15_000
    private static let heartbeatLock = NSLock()
    private static var lastHeartbeatWriteMs: Int64 = 0

    public static func isActive() -> Bool {
        prefs.bool(forKey: activeKey)
    }

    public static func isStale() -> Bool {
        // UserDefaults stores numbers as NSNumber; `as? Int64` often fails → false "last=0" stale.
        let last: Int64
        if let number = prefs.object(forKey: heartbeatKey) as? NSNumber {
            last = number.int64Value
        } else {
            last = 0
        }
        return currentWallClockMs() - last > staleAfterMs
    }

    /// Wall-clock milliseconds, NOT `ProcessInfo.systemUptime`. This value is persisted across
    /// process lifetimes (that is the entire point -- the BGTask watchdog reads it in a later
    /// process launch), and systemUptime resets to near-zero on every device reboot. A session
    /// killed by a reboot would have written a large systemUptime just before going down; a watchdog
    /// running after reboot would then compute `small_current - large_stale = negative`, which is
    /// less than staleAfterMs, so a definitely-dead session read as perfectly healthy -- exactly
    /// the case this mechanism exists to catch. Wall-clock time keeps moving forward through a
    /// reboot, so the elapsed-time comparison stays correct regardless of what rebooted in between.
    private static func currentWallClockMs() -> Int64 {
        Int64(Date().timeIntervalSince1970 * 1000)
    }
}
