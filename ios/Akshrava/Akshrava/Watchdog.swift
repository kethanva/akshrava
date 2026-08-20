//
//  Watchdog.swift
//  Akshrava iOS
//
//  Background watchdog using BGTaskScheduler — mirrors Android's WatchdogReceiver + AlarmManager.
//  BackgroundTasks is iOS 13+ only; on macOS (CI/SPM swift test) the scheduler is a no-op.
//

import Foundation
#if canImport(BackgroundTasks)
import BackgroundTasks
#endif
#if canImport(UserNotifications)
import UserNotifications
#endif

public class Watchdog {
    public static let shared = Watchdog()

    /// BGTaskScheduler task identifier — must match Info.plist BGTaskSchedulerPermittedIdentifiers.
    /// Registering an identifier absent from that array raises NSInternalInconsistencyException
    /// at launch, so the two must never drift apart.
    public static let taskIdentifier = "org.akshrava.ios.watchdog"

    /// How often the watchdog fires to check session health (mirrors Android Watchdog.INTERVAL_MS).
    /// iOS gives no interval guarantee for BGProcessingTaskRequest -- this is the requested
    /// earliest fire time, not a promise, unlike Android's AlarmManager equivalent.
    public static let intervalMs: Int64 = 3 * 60_000

    /// Message key for the press-Start prompt. The notification is best-effort; speech is required.
    public static let stalledRecoverySpeechKey = "watchdog_stalled"

    private init() {}

    /// Speak the recovery prompt. Android posts a notification *and* TTS; iOS has no notification
    /// authorization request, so a notification-only path is silence. Do not auto-start.
    public static func promptStaleSessionRecovery(language: String = "en") {
        AlertManager.shared.speak(
            messageKey: stalledRecoverySpeechKey,
            language: language,
            force: true
        )
    }

    /// Register the handler AND schedule the first request. Registration alone (the previous
    /// behaviour) never re-arms the task: BGTaskScheduler only fires a request that was actually
    /// submitted, so a session that died in the background had no recovery path at all.
    public func registerBackgroundTasks() {
        #if canImport(BackgroundTasks)
        // macOS 13 also vends BackgroundTasks, so the availability guard must name both platforms.
        // `canImport` alone is not enough: on a macOS build where the framework exists, an
        // iOS-only `#available` leaves macOS availability unconstrained and the BG* symbols below
        // fail to compile against the package's macOS 12 deployment target.
        if #available(iOS 13.0, macOS 13.0, *) {
            let registered = BGTaskScheduler.shared.register(
                forTaskWithIdentifier: Watchdog.taskIdentifier,
                using: nil
            ) { [weak self] task in
                guard let processingTask = task as? BGProcessingTask else {
                    task.setTaskCompleted(success: false)
                    return
                }
                self?.handle(task: processingTask)
            }
            AgentDebugLog.log(message: "watchdog_register ok=\(registered)")
            if !registered { AgentDebugLog.error(event: "watchdog_registration_failed") }
            scheduleNext()
        }
        #endif
        // On macOS: no-op (BGTaskScheduler is not available)
    }

    #if canImport(BackgroundTasks)
    @available(iOS 13.0, macOS 13.0, *)
    private final class TaskCompletionGate {
        private let lock = NSLock()
        private var completed = false

        func finish(_ task: BGTask, success: Bool) {
            lock.lock()
            guard !completed else {
                lock.unlock()
                return
            }
            completed = true
            lock.unlock()
            task.setTaskCompleted(success: success)
        }
    }

    @available(iOS 13.0, macOS 13.0, *)
    private func handle(task: BGProcessingTask) {
        // Re-arm before doing anything else: the request is one-shot, and if recovery below were
        // to crash or the task simply never called setTaskCompleted in time, a missed re-arm
        // would silently end all future recovery for the rest of the app's lifetime.
        scheduleNext()

        // Required by BGTaskScheduler: without an expiration handler iOS terminates the process
        // when the (short, unspecified) budget runs out, and repeatedly overrunning makes the
        // system deprioritise future scheduling for this identifier -- degrading the very
        // recovery path this task exists to provide.
        let completion = TaskCompletionGate()
        task.expirationHandler = {
            AgentDebugLog.error(event: "watchdog_task_expired")
            completion.finish(task, success: false)
        }

        // A session marked active whose heartbeat has gone stale was killed by the OS (backgrounded
        // camera session torn down, app suspended) rather than stopped by the user -- Stop always
        // clears the active flag first. Recovery here is best-effort: iOS gives no guarantee this
        // task runs promptly, or at all, while backgrounded.
        let recovered = SessionFlags.isActive() && SessionFlags.isStale()
        guard recovered else {
            completion.finish(task, success: true)
            return
        }
        AgentDebugLog.log(message: "watchdog_recovering_stale_session")
        SessionFlags.setActive(false)
        Watchdog.promptStaleSessionRecovery(language: ProvisionStore.load().language)
        #if canImport(UserNotifications)
        let content = UNMutableNotificationContent()
        content.title = "Akshrava Assistance Stopped"
        content.body = AlertManager.shared.resolvedTemplate(
            messageKey: Watchdog.stalledRecoverySpeechKey,
            language: ProvisionStore.load().language
        )?.text ?? "Open Akshrava and press Start assistance again. Keep using your cane."
        let request = UNNotificationRequest(identifier: "watchdog.stalled", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { _ in
            completion.finish(task, success: true)
        }
        #else
        completion.finish(task, success: true)
        #endif
    }

    @available(iOS 13.0, macOS 13.0, *)
    private func scheduleNext() {
        let request = BGProcessingTaskRequest(identifier: Watchdog.taskIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: Double(Watchdog.intervalMs) / 1000)
        request.requiresNetworkConnectivity = false
        request.requiresExternalPower = false
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            // Never a fatal path: BGTaskScheduler.submit can throw (too many pending requests,
            // simulator, disabled background refresh). The foreground session is unaffected.
            AgentDebugLog.error(event: "watchdog_submit_failed", detail: error.localizedDescription)
        }
    }
    #endif
}
