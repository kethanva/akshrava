package org.akshrava.app

import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Timing invariants for a sustained walking session.
 *
 * Field reports: assistance appears to stop after about three minutes and needs a manual
 * Stop/Start. Three minutes is exactly [Watchdog.INTERVAL_MS] and [SessionFlags.STALE_AFTER_MS],
 * so these tests pin the relationships between the timers that decide whether a healthy session
 * is mistaken for a dead one. The previous WatchdogTest asserted only that a constant equalled
 * itself, which could not catch a regression in any of these relationships.
 *
 * Target: a session must stay continuously healthy for at least [TARGET_SESSION_MS].
 */
class SessionDurationTest {
    private companion object {
        const val TARGET_SESSION_MS = 15 * 60_000L
    }

    @Test
    fun heartbeatIsFrequentEnoughThatAHealthySessionIsNeverCalledStale() {
        // The watchdog wakes every INTERVAL_MS and prompts the user to restart if the last
        // heartbeat is older than STALE_AFTER_MS. A healthy session writes a heartbeat every
        // HEARTBEAT_INTERVAL_MS, so that must leave generous margin — otherwise ordinary
        // scheduling jitter produces a spurious "assistance stopped" alarm mid-walk.
        assertTrue(
            "heartbeat interval must be well inside the stale window",
            AssistService.HEARTBEAT_INTERVAL_MS * 3 <= SessionFlags.STALE_AFTER_MS
        )
    }

    @Test
    fun recoverableCameraStallHealsBeforeTheWatchdogCallsTheSessionDead() {
        // A stalled camera is rebound automatically after CAMERA_STALL_REBIND_MS. That recovery
        // must complete well before the staleness threshold, or a self-healing hiccup escalates
        // into a spoken restart prompt the user cannot ignore.
        val worstCaseRecoveryMs =
            AssistService.CAMERA_STALL_REBIND_MS + AssistService.CAMERA_STALL_CHECK_MS +
                AssistService.HEARTBEAT_INTERVAL_MS
        assertTrue(
            "camera stall recovery ($worstCaseRecoveryMs ms) must finish inside the stale " +
                "window (${SessionFlags.STALE_AFTER_MS} ms)",
            worstCaseRecoveryMs * 2 <= SessionFlags.STALE_AFTER_MS
        )
    }

    @Test
    fun stallDetectorPollsSeveralTimesPerRebindWindow() {
        assertTrue(
            "the stall check must sample several times per rebind window to react promptly",
            AssistService.CAMERA_STALL_CHECK_MS * 2 <= AssistService.CAMERA_STALL_REBIND_MS
        )
    }

    @Test
    fun wakeLockOutlastsATargetLengthSession() {
        // The partial wake lock is deliberately timed so a hung teardown cannot hold the CPU
        // forever, but it must not expire during a normal walk and let the CPU sleep mid-session.
        assertTrue(
            "wake lock (${AssistService.WAKE_LOCK_TIMEOUT_MS} ms) must outlast a " +
                "$TARGET_SESSION_MS ms session",
            AssistService.WAKE_LOCK_TIMEOUT_MS >= TARGET_SESSION_MS * 2
        )
    }

    @Test
    fun watchdogKeepsCheckingForTheWholeSession() {
        // The alarm is one-shot; WatchdogReceiver reschedules it on every fire. Over a target
        // session that is several wake-ups, and each one must re-arm or liveness checking
        // silently stops partway through the walk.
        val wakeUps = TARGET_SESSION_MS / Watchdog.INTERVAL_MS
        assertTrue("watchdog must wake repeatedly across a session, got $wakeUps", wakeUps >= 4)
    }

    @Test
    fun appPingKeepsTheServerAdmissionLeaseAliveWhenTheUserStandsStill() {
        // A stationary user's frames are duplicate-dropped on the phone, so the only traffic
        // renewing the server's session lease is ProtocolClient's application-level ping. The
        // server lease is 180 s (session_admission.DEFAULT_LEASE_SECONDS); pinging must happen
        // several times per lease so one dropped ping cannot evict a live walking session.
        val serverLeaseMs = 180_000L
        assertTrue(
            "app ping (${ProtocolClient.APP_PING_INTERVAL_MS} ms) must renew well inside the " +
                "$serverLeaseMs ms server lease",
            ProtocolClient.APP_PING_INTERVAL_MS * 3 <= serverLeaseMs
        )
    }

    @Test
    fun settleTimeoutCannotOutlastTheStaleWindow() {
        // A frame that never settles blocks the in-flight slot. That must resolve far inside the
        // stale window, otherwise one hung frame cascades into a "session dead" prompt.
        assertTrue(
            "settle timeout must resolve well inside the stale window",
            ProtocolClient.FRAME_SETTLE_TIMEOUT_MS * ProtocolClient.SETTLE_TIMEOUTS_BEFORE_RECONNECT
                < SessionFlags.STALE_AFTER_MS
        )
    }

    @Test
    fun qualityDrivenCameraRebindsAreRateLimited() {
        // The backend quality ladder steps at 150 ms of inference and the live deployment runs
        // 129-324 ms, so the advised max_side flips across that rung constantly. Each flip is a
        // full CameraX unbind/rebind costing 1-2 s of frames — observed live as 640 -> 512 -> 640
        // within three seconds. The cooldown must be long enough that oscillation across one
        // rung boundary cannot translate into repeated camera restarts.
        assertTrue(
            "quality rebinds must be spaced further apart than the frame cadence",
            AssistService.MIN_QUALITY_REBIND_INTERVAL_MS >= 5_000L
        )
        // ...but never so long that it delays genuine stall recovery, which bypasses the
        // cooldown entirely and must still act inside the stale window.
        assertTrue(
            "stall recovery must remain far faster than the rebind cooldown allows for quality",
            AssistService.CAMERA_STALL_REBIND_MS < SessionFlags.STALE_AFTER_MS
        )
    }
}
