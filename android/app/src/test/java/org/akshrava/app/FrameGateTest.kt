package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FrameGateTest {
    @Test
    fun duplicateGateRequiresMatchingLowDifferenceThumbnails() {
        val previous = IntArray(32 * 32) { 100 }
        val nearlyIdentical = IntArray(32 * 32) { 105 }

        assertTrue(FrameGate.isDuplicate(previous, nearlyIdentical))
        assertFalse(FrameGate.isDuplicate(previous, IntArray(31 * 32) { 100 }))
        assertFalse(FrameGate.isDuplicate(null, nearlyIdentical))
    }

    @Test
    fun blurGateSeparatesFlatAndDetailedThumbnails() {
        assertTrue(FrameGate.isBlurred(IntArray(32 * 32) { 128 }))

        val checkerboard = IntArray(32 * 32) { index -> if ((index / 32 + index % 32) % 2 == 0) 0 else 255 }
        assertFalse(FrameGate.isBlurred(checkerboard))
    }
}
