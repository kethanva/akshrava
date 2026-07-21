package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Policy tests for TTS engine recovery.
 *
 * Live-reproduced failure (OnePlus HD1901): force-stopping com.google.android.tts leaves the
 * framework's TextToSpeech client permanently unbound — every speak() returns ERROR ("speak
 * failed: not bound to TTS engine") while detection keeps running. The old code swallowed the
 * ERROR, so the app went silently mute until a manual Stop/Start rebuilt AlertManager. These
 * tests pin the recovery gate that fixes that: rebuilds must be allowed after engine death,
 * rate-limited so a broken engine cannot become a rebuild storm, and replenished on success so
 * recovery keeps working across a long walk.
 */
class EngineRebuildPolicyTest {
    @Test
    fun firstFailureTriggersImmediateRebuild() {
        // lastRebuildMs == 0 marks "never rebuilt": the first engine death must recover at once,
        // not wait out an interval that never started.
        assertTrue(AlertManager.engineRebuildAllowed(nowMs = 5_000L, lastRebuildMs = 0L, streak = 0))
    }

    @Test
    fun rebuildsAreRateLimited() {
        val first = 10_000L
        assertFalse(
            "a second rebuild inside the interval floor must be suppressed",
            AlertManager.engineRebuildAllowed(
                nowMs = first + AlertManager.ENGINE_REBUILD_MIN_INTERVAL_MS - 1,
                lastRebuildMs = first,
                streak = 1
            )
        )
        assertTrue(
            "after the floor elapses the next rebuild may proceed",
            AlertManager.engineRebuildAllowed(
                nowMs = first + AlertManager.ENGINE_REBUILD_MIN_INTERVAL_MS,
                lastRebuildMs = first,
                streak = 1
            )
        )
    }

    @Test
    fun streakExhaustionStopsTheRebuildLoop() {
        // A hard-broken engine (init fails every time) must not loop forever; haptics remain
        // the surviving channel once the quota is spent.
        assertFalse(
            AlertManager.engineRebuildAllowed(
                nowMs = 1_000_000L,
                lastRebuildMs = 0L,
                streak = AlertManager.ENGINE_REBUILD_MAX_STREAK
            )
        )
    }

    @Test
    fun successResetsTheStreakSoRecoveryWorksRepeatedly() {
        // The streak resets to 0 on a successful speak hand-off (speak() SUCCESS). An OEM that
        // kills the engine every few minutes for a whole walk must be survivable: streak 0 with
        // an old lastRebuild timestamp must always allow recovery again.
        assertTrue(
            AlertManager.engineRebuildAllowed(
                nowMs = 30 * 60_000L,
                lastRebuildMs = 25 * 60_000L,
                streak = 0
            )
        )
    }

    @Test
    fun quotaCoversRepeatedEngineDeathsAcrossATargetSession() {
        // 15-minute target walk with the observed OEM kill cadence (~ every 3 minutes): five
        // deaths, each needing one successful rebuild. Streak resets on success, so the per-burst
        // quota only needs to beat transient double-failures, but it must be at least 2 to
        // survive an engine that needs one warm-up attempt after a cold kill.
        assertTrue(AlertManager.ENGINE_REBUILD_MAX_STREAK >= 2)
        // And the interval floor must stay far below the utterance cadence a walk produces,
        // or recovery would arrive after the hazard has passed.
        assertTrue(AlertManager.ENGINE_REBUILD_MIN_INTERVAL_MS <= 10_000L)
    }
}
