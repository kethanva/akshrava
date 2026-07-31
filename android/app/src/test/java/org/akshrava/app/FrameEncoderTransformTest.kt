package org.akshrava.app

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import java.nio.ByteBuffer

class FrameEncoderTransformTest {

    /**
     * Per-pixel reference copy — the shape the encoder used before rows were read in bulk.
     * The optimised path must agree with it byte for byte on every stride layout below.
     */
    private fun referenceCopyPlane(
        buffer: ByteBuffer,
        rowStride: Int,
        pixelStride: Int,
        width: Int,
        height: Int,
        out: ByteArray,
        offset: Int,
        outputStride: Int,
    ) {
        val duplicate = buffer.duplicate()
        val base = duplicate.position()
        for (row in 0 until height) {
            for (column in 0 until width) {
                val inputIndex = row * rowStride + column * pixelStride
                out[offset + (row * width + column) * outputStride] = duplicate.get(base + inputIndex)
            }
        }
    }

    /** Buffer sized exactly as Android documents a YUV_420_888 plane, so over-reads fail loudly. */
    private fun planeBuffer(rowStride: Int, pixelStride: Int, width: Int, height: Int): ByteBuffer {
        val capacity = rowStride * (height - 1) + (width - 1) * pixelStride + 1
        val buffer = ByteBuffer.allocateDirect(capacity)
        for (i in 0 until capacity) buffer.put(i, (i * 31 % 251).toByte())
        return buffer
    }

    private fun assertPlaneCopyMatchesReference(
        rowStride: Int,
        pixelStride: Int,
        width: Int,
        height: Int,
        outputStride: Int,
        offset: Int = 0,
    ) {
        val buffer = planeBuffer(rowStride, pixelStride, width, height)
        val size = offset + width * height * outputStride
        val expected = ByteArray(size)
        val actual = ByteArray(size)

        referenceCopyPlane(buffer, rowStride, pixelStride, width, height, expected, offset, outputStride)
        FrameEncoder.copyPlaneInto(
            buffer, rowStride, pixelStride, width, height, actual, offset, outputStride,
            ByteArray((width - 1) * pixelStride + 1),
        )

        assertArrayEquals(
            "rowStride=$rowStride pixelStride=$pixelStride ${width}x$height outputStride=$outputStride",
            expected,
            actual,
        )
    }

    @Test
    fun copyPlaneMatchesReferenceForPackedLuma() {
        // Contiguous fast path: rowStride == width, both strides 1 — one bulk read of the plane.
        assertPlaneCopyMatchesReference(rowStride = 16, pixelStride = 1, width = 16, height = 8, outputStride = 1)
    }

    @Test
    fun copyPlaneMatchesReferenceForPaddedLumaRows() {
        // OEM luma planes routinely pad each row past the visible width.
        assertPlaneCopyMatchesReference(rowStride = 24, pixelStride = 1, width = 16, height = 8, outputStride = 1)
    }

    @Test
    fun copyPlaneMatchesReferenceForSemiPlanarChroma() {
        // Interleaved UV: pixelStride 2 on both sides, which is the common NV12/NV21 layout.
        assertPlaneCopyMatchesReference(rowStride = 16, pixelStride = 2, width = 8, height = 4, outputStride = 2)
    }

    @Test
    fun copyPlaneMatchesReferenceForPlanarChroma() {
        // Fully planar I420 chroma read into an interleaved NV21 destination.
        assertPlaneCopyMatchesReference(rowStride = 10, pixelStride = 1, width = 8, height = 4, outputStride = 2)
    }

    @Test
    fun copyPlaneWritesChromaAtTheRequestedOffset() {
        // V and U start at frameSize and frameSize+1; a wrong offset silently swaps the colours.
        assertPlaneCopyMatchesReference(
            rowStride = 16, pixelStride = 2, width = 8, height = 4, outputStride = 2, offset = 5,
        )
    }

    @Test
    fun copyPlaneReadsNoBytesBeyondTheDocumentedPlaneSize() {
        // The last row of a tightly-sized plane holds (width-1)*pixelStride + 1 bytes, not a full
        // rowStride. Reading a whole stride there overruns the buffer on real devices.
        val rowStride = 16
        val pixelStride = 2
        val width = 8
        val height = 4
        val buffer = planeBuffer(rowStride, pixelStride, width, height)
        assertEquals(rowStride * (height - 1) + (width - 1) * pixelStride + 1, buffer.capacity())

        val out = ByteArray(width * height * 2)
        FrameEncoder.copyPlaneInto(
            buffer, rowStride, pixelStride, width, height, out, 0, 2,
            ByteArray((width - 1) * pixelStride + 1),
        )

        assertEquals(buffer.get(rowStride * (height - 1) + (width - 1) * pixelStride), out[out.size - 2])
    }

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
        assertEquals(11.toByte(), dst[1])
        assertEquals(22.toByte(), dst[3 * 2 + 1])
        assertEquals(src.size, dst.size)
    }

    @Test
    fun rotateNv21By180And270PreservesFrameSize() {
        val width = 4
        val height = 2
        val src = ByteArray(width * height * 3 / 2) { index -> index.toByte() }
        val dst180 = ByteArray(src.size)
        val dst270 = ByteArray(src.size)

        FrameEncoder.rotateNv21(src, width, height, 180, dst180)
        assertEquals(src.size, dst180.size)

        FrameEncoder.rotateNv21(src, width, height, 270, dst270)
        assertEquals(src.size, dst270.size)
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
        assertArrayEquals(byteArrayOf(1, 7, 7, 7), dst.copyOfRange(0, 4))
    }

    @Test
    fun compressPreparedExecutesWithoutException() {
        val encoder = FrameEncoder()
        val nv21 = ByteArray(4 * 4 * 3 / 2) { 10 }
        val prepared = PreparedFrame(nv21, 4, 4)
        val encoded = encoder.compressPrepared(prepared, quality = 55)

        assertNotNull(encoded)
        assertEquals(4, encoded.width)
        assertEquals(4, encoded.height)
    }
}
