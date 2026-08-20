package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AnalyzeFailurePolicyTest {
    @Test
    fun firstFailureIsSilent() {
        assertFalse(
            AssistService.shouldAnnounceAnalyzeFailure(
                nowMs = 10_000L, consecutiveFailures = 1, lastAnnounceMs = 0L
            )
        )
    }

    @Test
    fun secondConsecutiveFailureSpeaksOnce() {
        assertTrue(
            AssistService.shouldAnnounceAnalyzeFailure(
                nowMs = 10_000L, consecutiveFailures = 2, lastAnnounceMs = 0L
            )
        )
    }

    @Test
    fun repeatFailuresAreDebouncedByTheCooldown() {
        val spokenAt = 10_000L
        assertFalse(
            AssistService.shouldAnnounceAnalyzeFailure(
                nowMs = spokenAt + AssistService.ANALYZE_FAILURE_ANNOUNCE_COOLDOWN_MS - 1,
                consecutiveFailures = 3,
                lastAnnounceMs = spokenAt
            )
        )
        assertTrue(
            AssistService.shouldAnnounceAnalyzeFailure(
                nowMs = spokenAt + AssistService.ANALYZE_FAILURE_ANNOUNCE_COOLDOWN_MS,
                consecutiveFailures = 3,
                lastAnnounceMs = spokenAt
            )
        )
    }

    @Test
    fun fiveConsecutiveFailuresReachTheStopThreshold() {
        assertEquals(5, AssistService.ANALYZE_FAILURES_BEFORE_STOP)
        assertTrue(
            AssistService.shouldAnnounceAnalyzeFailure(
                nowMs = 20_000L,
                consecutiveFailures = AssistService.ANALYZE_FAILURES_BEFORE_STOP,
                lastAnnounceMs = 0L
            )
        )
    }
}
