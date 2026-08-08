//
//  Watchdog.swift
//  Akshrava iOS
//
//  Background watchdog using BGTaskScheduler — mirrors Android's WatchdogReceiver + AlarmManager.
//  BGTaskScheduler is iOS-only; on macOS (CI/SPM swift test) registration is a no-op.
//

import Foundation
#if os(iOS)
import BackgroundTasks
#endif

public class Watchdog {
    public static let shared = Watchdog()

    /// BGTaskScheduler task identifier — must match Info.plist BGTaskSchedulerPermittedIdentifiers.
    public static let taskIdentifier = "org.akshrava.ios.watchdog"

    /// How often the watchdog fires to check session health (mirrors Android Watchdog.INTERVAL_MS).
    public static let intervalMs: Int64 = 3 * 60_000

    private init() {}

    public func registerBackgroundTasks() {
        #if os(iOS)
        if #available(iOS 13.0, *) {
            BGTaskScheduler.shared.register(
                forTaskWithIdentifier: Watchdog.taskIdentifier,
                queue: nil
            ) { task in
                task.setTaskCompleted(success: true)
            }
        }
        #endif
    }
}
