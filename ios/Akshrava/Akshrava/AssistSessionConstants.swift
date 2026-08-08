//
//  AssistSessionConstants.swift
//  Akshrava iOS
//
//  Session lifecycle timing constants — mirrors the companion object constants in
//  Android's AssistService.kt. These are used by session duration invariant tests
//  to verify that heartbeat, stall detection, wake-keep-alive, and watchdog timers
//  are properly related to each other.
//

import Foundation

/// Timing constants for an active assistance session.
/// All values in milliseconds unless otherwise noted.
public enum AssistSessionConstants {
    /// How often SessionFlags writes a heartbeat to UserDefaults while assistance is running.
    public static let heartbeatIntervalMs: Int64 = 30_000

    /// Upper bound on an acquired screen idle-timer / keep-alive hold.
    /// Must outlast a typical walking session (15+ minutes) × 2 for safety.
    public static let keepAliveDurationMs: Int64 = 60 * 60_000

    /// How often the keep-alive is renewed to prevent it expiring mid-session.
    public static let keepAliveRenewIntervalMs: Int64 = 15 * 60_000

    /// Maximum wait before treating a stalled camera as needing a restart.
    public static let cameraStallRebindMs: Int64 = 15_000

    /// How often the camera stall detector polls for a frozen output.
    public static let cameraStallCheckMs: Int64 = 5_000

    /// Minimum gap between quality-driven camera restarts (resolution ladder changes).
    public static let minQualityRebindIntervalMs: Int64 = 10_000
}
