package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SoftShedAnnouncementTest {
    @Test
    fun oneShedIsSilent() {
        assertFalse(
            ProtocolClient.shouldAnnounceSoftShed(
                consecutiveSoftSheds = 1, lastAnnounceAtMonoMs = 0L, nowMonoMs = 10_000L
            )
        )
    }

    @Test
    fun thirdConsecutiveShedSpeaksOnce() {
        assertTrue(
            ProtocolClient.shouldAnnounceSoftShed(
                consecutiveSoftSheds = ProtocolClient.SOFT_SHED_ANNOUNCE_AFTER,
                lastAnnounceAtMonoMs = 0L,
                nowMonoMs = 10_000L
            )
        )
    }

    @Test
    fun announcementIsBoundedByTheCooldown() {
        val spokenAt = 10_000L
        assertFalse(
            ProtocolClient.shouldAnnounceSoftShed(
                consecutiveSoftSheds = 4,
                lastAnnounceAtMonoMs = spokenAt,
                nowMonoMs = spokenAt + ProtocolClient.SOFT_SHED_ANNOUNCE_COOLDOWN_MS - 1
            )
        )
        assertTrue(
            ProtocolClient.shouldAnnounceSoftShed(
                consecutiveSoftSheds = 4,
                lastAnnounceAtMonoMs = spokenAt,
                nowMonoMs = spokenAt + ProtocolClient.SOFT_SHED_ANNOUNCE_COOLDOWN_MS
            )
        )
    }

    @Test
    fun aFreshResultResetsTheShedCounter() {
        // A successful result zeros consecutiveSoftSheds; a zero counter must not speak.
        assertFalse(
            ProtocolClient.shouldAnnounceSoftShed(
                consecutiveSoftSheds = 0, lastAnnounceAtMonoMs = 0L, nowMonoMs = 10_000L
            )
        )
    }
}
