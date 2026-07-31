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

    @Test
    fun occlusionGateOnlyFiresOnNearBlack() {
        assertTrue(FrameGate.isOccluded(IntArray(32 * 32) { 4 }))
        assertFalse(FrameGate.isOccluded(IntArray(32 * 32) { 40 }))
    }

    @Test
    fun occlusionGateLeavesDimButUsableScenesAlone() {
        // Dusk, an unlit corridor, a shaded underpass. An occlusion verdict here drops the frame,
        // so assistance would stop in exactly the conditions the user most needs it.
        assertFalse(FrameGate.isOccluded(IntArray(32 * 32) { 12 }))
        assertFalse(FrameGate.isOccluded(IntArray(32 * 32) { 20 }))
    }

    @Test
    fun occlusionGateTreatsEmptyThumbnailAsOccluded() {
        assertTrue(FrameGate.isOccluded(IntArray(0)))
    }

    @Test
    fun glareGateRequiresBrightAndFlatField() {
        assertTrue(FrameGate.isGlared(IntArray(32 * 32) { 245 }))
        // Bright but structured (checkerboard) must not count as glare.
        val brightChecker = IntArray(32 * 32) { index ->
            if ((index / 32 + index % 32) % 2 == 0) 200 else 255
        }
        assertFalse(FrameGate.isGlared(brightChecker))
        // Dim flat field is occlusion/blur territory, not glare.
        assertFalse(FrameGate.isGlared(IntArray(32 * 32) { 100 }))
    }
}
