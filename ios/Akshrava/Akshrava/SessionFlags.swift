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
        prefs.set(active, forKey: activeKey)
        prefs.set(currentMonoMs(), forKey: heartbeatKey)
    }

    public static func heartbeat() {
        prefs.set(currentMonoMs(), forKey: heartbeatKey)
    }

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
        return currentMonoMs() - last > staleAfterMs
    }

    private static func currentMonoMs() -> Int64 {
        Int64(ProcessInfo.processInfo.systemUptime * 1000)
    }
}
