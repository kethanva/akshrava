package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AwarenessTextGuardTest {
    @Test
    fun serverLookSummaryIsSpeakable() {
        assertTrue(
            ProtocolClient.awarenessTextIsSpeakable(
                "No alert in this recent view. Continue using cane or guide"
            )
        )
    }

    @Test
    fun rejectsSafeClearCrossNavigateCollisionApproach() {
        for (word in listOf("safe", "clear", "cross", "navigate", "collision", "approach")) {
            assertFalse(
                "must reject $word",
                ProtocolClient.awarenessTextIsSpeakable("Hazard $word nearby")
            )
        }
    }

    @Test
    fun rejectsInflections() {
        assertFalse(ProtocolClient.awarenessTextIsSpeakable("Walk safely"))
        assertFalse(ProtocolClient.awarenessTextIsSpeakable("Watch for collisions"))
        assertFalse(ProtocolClient.awarenessTextIsSpeakable("Keep navigating"))
    }

    @Test
    fun blankTextIsNotSpeakable() {
        assertFalse(ProtocolClient.awarenessTextIsSpeakable(""))
        assertFalse(ProtocolClient.awarenessTextIsSpeakable("   "))
    }
}
