package org.akshrava.app

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class FrameEncoderTransformTest {
    @Test
    fun normalizeRotationClampsToQuarterTurns() {
        assertEquals(0, FrameEncoder.normalizeRotation(0))
        assertEquals(90, FrameEncoder.normalizeRotation(450))
        assertEquals(270, FrameEncoder.normalizeRotation(-90))
        assertEquals(0, FrameEncoder.normalizeRotation(45))
    }

    @Test
    fun rotateNv21By90MovesCornerLumaAndPreservesFrameSize() {
        val width = 4
        val height = 2
        val src = ByteArray(width * height * 3 / 2) { index -> index.toByte() }
        src[0] = 11
        src[width - 1] = 22
        val dst = ByteArray(src.size)
        FrameEncoder.rotateNv21(src, width, height, 90, dst)
        // (0,0) -> (1,0) in the 2x4 destination (newWidth=height=2)
        assertEquals(11.toByte(), dst[1])
        // (3,0) -> (1,3) => index 3*2+1
        assertEquals(22.toByte(), dst[3 * 2 + 1])
        assertEquals(src.size, dst.size)
    }

    @Test
    fun scaleNv21DownsamplesEvenDimensions() {
        val srcWidth = 4
        val srcHeight = 4
        val src = ByteArray(srcWidth * srcHeight * 3 / 2) { 7 }
        src[0] = 1
        src[srcWidth * srcHeight - 1] = 9
        val dst = ByteArray(2 * 2 * 3 / 2)
        FrameEncoder.scaleNv21(src, srcWidth, srcHeight, 2, 2, dst)
        assertEquals(1.toByte(), dst[0])
        assertEquals(6, dst.size)
        // Nearest samples: (0,0),(2,0),(0,2),(2,2) — corner (3,3)=9 is not selected.
        assertArrayEquals(byteArrayOf(1, 7, 7, 7), dst.copyOfRange(0, 4))
    }
}
