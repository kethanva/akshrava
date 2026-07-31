package org.akshrava.app

import android.graphics.Rect
import android.graphics.YuvImage
import androidx.camera.core.ImageProxy
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import kotlin.math.roundToInt

data class EncodedFrame(val jpeg: ByteArray, val width: Int, val height: Int)

/** NV21 plane ready for JPEG after rotation/downscale; valid until the next [FrameEncoder.prepare]. */
data class PreparedFrame(val nv21: ByteArray, val width: Int, val height: Int)

/**
 * CPU-only conversion scoped to one assistance-service lifecycle.
 *
 * FrameEncoder is not thread-safe: AssistService runs analysis on a single-thread executor
 * and must be the only caller of [encode] / [prepare] / [compressPrepared]. Scratch buffers
 * are reused across frames.
 *
 * Keeps all orientation and downscale work in NV21 so we never allocate Bitmaps
 * (and the GC pauses that follow) on the analysis thread. Prefer [prepare] then close the
 * ImageProxy before [compressPrepared] so CameraX can advance while JPEG runs.
 */
class FrameEncoder {
    // Analysis is single-threaded (AssistService frameExecutor). Do not call encode() from
    // additional threads without external synchronization — scratch buffers would race.
    private var nv21Scratch = ByteArray(0)
    private var transformScratch = ByteArray(0)
    private var rowScratch = ByteArray(0)
    private val jpegScratch = ByteArrayOutputStream(64 * 1024)

    fun encode(image: ImageProxy, maxSide: Int, quality: Int): EncodedFrame =
        compressPrepared(prepare(image, maxSide), quality)

    /**
     * Copy/rotate/downscale from [image] into reusable NV21 scratch. Caller may close the
     * ImageProxy immediately afterward, then call [compressPrepared].
     */
    fun prepare(image: ImageProxy, maxSide: Int): PreparedFrame {
        var width = image.width
        var height = image.height
        var nv21 = toNv21(image)
        val rotation = normalizeRotation(image.imageInfo.rotationDegrees)

        if (rotation != 0) {
            val required = width * height * 3 / 2
            ensureTransformCapacity(required)
            rotateNv21(nv21, width, height, rotation, transformScratch)
            if (rotation == 90 || rotation == 270) {
                val swapped = width
                width = height
                height = swapped
            }
            nv21 = promoteTransformScratch()
        }

        if (maxOf(width, height) > maxSide) {
            val scale = maxSide.toFloat() / maxOf(width, height).toFloat()
            val newWidth = (width * scale).roundToInt().coerceAtLeast(1)
            val newHeight = (height * scale).roundToInt().coerceAtLeast(1)
            // Even dimensions keep NV21 chroma alignment valid for YuvImage.
            val evenWidth = (newWidth - newWidth % 2).coerceAtLeast(2)
            val evenHeight = (newHeight - newHeight % 2).coerceAtLeast(2)
            val required = evenWidth * evenHeight * 3 / 2
            ensureTransformCapacity(required)
            scaleNv21(nv21, width, height, evenWidth, evenHeight, transformScratch)
            nv21 = promoteTransformScratch()
            width = evenWidth
            height = evenHeight
        }

        return PreparedFrame(nv21, width, height)
    }

    /**
     * Make the just-written transform buffer the live plane by swapping the two scratch roles.
     *
     * Each transform stage writes into [transformScratch]; the live plane is expected to live in
     * [nv21Scratch]. Copying it back cost a full-frame memcpy per stage per frame — up to two per
     * frame at ~1.4 MB apiece for a 1280x960 analysis buffer — purely to preserve which field
     * held the bytes. Swapping the references is equivalent and free: the previous plane is not
     * needed once a stage has consumed it, and it becomes the scratch the next stage writes into.
     */
    private fun promoteTransformScratch(): ByteArray {
        val produced = transformScratch
        transformScratch = nv21Scratch
        nv21Scratch = produced
        return produced
    }

    fun compressPrepared(prepared: PreparedFrame, quality: Int): EncodedFrame =
        EncodedFrame(compress(prepared.nv21, prepared.width, prepared.height, quality), prepared.width, prepared.height)

    private fun compress(nv21: ByteArray, width: Int, height: Int, quality: Int): ByteArray {
        jpegScratch.reset()
        YuvImage(nv21, android.graphics.ImageFormat.NV21, width, height, null)
            .compressToJpeg(Rect(0, 0, width, height), quality.coerceIn(25, 95), jpegScratch)
        return jpegScratch.toByteArray()
    }

    // NOT thread-safe: toNv21 writes directly to nv21Scratch. Only call from the
    // single-thread frameExecutor (AssistService). @Synchronized was removed to avoid
    // giving callers a false impression of concurrent safety.
    private fun toNv21(image: ImageProxy): ByteArray {
        val width = image.width
        val height = image.height
        val required = width * height * 3 / 2
        ensureNv21Capacity(required)
        val result = nv21Scratch
        copyPlane(image.planes[0].buffer, image.planes[0].rowStride, image.planes[0].pixelStride,
            width, height, result, 0, 1)
        // NV21 requires V then U. CameraX plane order is Y, U, V.
        copyPlane(image.planes[2].buffer, image.planes[2].rowStride, image.planes[2].pixelStride,
            width / 2, height / 2, result, width * height, 2)
        copyPlane(image.planes[1].buffer, image.planes[1].rowStride, image.planes[1].pixelStride,
            width / 2, height / 2, result, width * height + 1, 2)
        return result
    }

    private fun ensureNv21Capacity(required: Int) {
        if (nv21Scratch.size < required) nv21Scratch = ByteArray(required)
    }

    private fun ensureTransformCapacity(required: Int) {
        if (transformScratch.size < required) transformScratch = ByteArray(required)
    }

    private fun copyPlane(
        buffer: ByteBuffer, rowStride: Int, pixelStride: Int, width: Int, height: Int,
        out: ByteArray, offset: Int, outputStride: Int
    ) {
        val rowBytes = (width - 1) * pixelStride + 1
        if (rowScratch.size < rowBytes) rowScratch = ByteArray(rowBytes)
        copyPlaneInto(buffer, rowStride, pixelStride, width, height, out, offset, outputStride, rowScratch)
    }

    companion object {
        /**
         * Copy one YUV_420_888 plane into [out], writing every [outputStride]-th byte from [offset].
         *
         * Reads a whole source row per [ByteBuffer.get] call instead of one byte at a time. The
         * camera hands back a *direct* buffer, where every single-index `get` is a bounds-checked
         * off-heap read; doing that per pixel meant roughly 1.8 million of them per frame at a
         * 1280x960 analysis resolution, all on the one analysis thread that also rotates, scales,
         * JPEG-encodes and sends. Bulk reads turn each row into a memcpy, which is where the cost
         * belongs on the recycled hardware this runs on.
         *
         * [rowScratch] must hold at least `(width - 1) * pixelStride + 1` bytes. That is exactly
         * the documented extent of a plane's last row — reading a full [rowStride] there would
         * overrun a tightly-sized buffer.
         */
        fun copyPlaneInto(
            buffer: ByteBuffer, rowStride: Int, pixelStride: Int, width: Int, height: Int,
            out: ByteArray, offset: Int, outputStride: Int, rowScratch: ByteArray,
        ) {
            if (width <= 0 || height <= 0) return
            val source = buffer.duplicate()
            val base = source.position()

            // Packed source into a packed destination (the usual luma case): one memcpy, no loop.
            if (pixelStride == 1 && outputStride == 1 && rowStride == width) {
                source.position(base)
                source.get(out, offset, width * height)
                return
            }

            val rowBytes = (width - 1) * pixelStride + 1
            require(rowScratch.size >= rowBytes) { "rowScratch too small for one plane row" }
            for (row in 0 until height) {
                source.position(base + row * rowStride)
                source.get(rowScratch, 0, rowBytes)
                var destination = offset + row * width * outputStride
                if (pixelStride == 1 && outputStride == 1) {
                    System.arraycopy(rowScratch, 0, out, destination, width)
                    continue
                }
                var read = 0
                for (column in 0 until width) {
                    out[destination] = rowScratch[read]
                    read += pixelStride
                    destination += outputStride
                }
            }
        }

        fun normalizeRotation(degrees: Int): Int {
            var value = degrees % 360
            if (value < 0) value += 360
            return when (value) {
                0, 90, 180, 270 -> value
                else -> 0
            }
        }

        /** Clockwise rotation of an NV21 frame into [dst]. [dst] must hold width*height*3/2 bytes. */
        fun rotateNv21(src: ByteArray, width: Int, height: Int, degrees: Int, dst: ByteArray) {
            when (normalizeRotation(degrees)) {
                0 -> System.arraycopy(src, 0, dst, 0, width * height * 3 / 2)
                90 -> rotateNv21By90(src, width, height, dst)
                180 -> rotateNv21By180(src, width, height, dst)
                270 -> rotateNv21By270(src, width, height, dst)
            }
        }

        fun scaleNv21(
            src: ByteArray,
            srcWidth: Int,
            srcHeight: Int,
            dstWidth: Int,
            dstHeight: Int,
            dst: ByteArray,
        ) {
            require(srcWidth > 0 && srcHeight > 0 && dstWidth > 0 && dstHeight > 0)
            require(dstWidth % 2 == 0 && dstHeight % 2 == 0)
            val ySrcSize = srcWidth * srcHeight
            for (y in 0 until dstHeight) {
                val srcY = y * srcHeight / dstHeight
                for (x in 0 until dstWidth) {
                    val srcX = x * srcWidth / dstWidth
                    dst[y * dstWidth + x] = src[srcY * srcWidth + srcX]
                }
            }
            val dstYSize = dstWidth * dstHeight
            val srcUvRow = srcWidth
            val dstUvRow = dstWidth
            for (y in 0 until dstHeight / 2) {
                val srcY = y * (srcHeight / 2) / (dstHeight / 2)
                for (x in 0 until dstWidth / 2) {
                    val srcX = x * (srcWidth / 2) / (dstWidth / 2)
                    val srcIndex = ySrcSize + srcY * srcUvRow + srcX * 2
                    val dstIndex = dstYSize + y * dstUvRow + x * 2
                    dst[dstIndex] = src[srcIndex]
                    dst[dstIndex + 1] = src[srcIndex + 1]
                }
            }
        }

        private fun rotateNv21By90(src: ByteArray, width: Int, height: Int, dst: ByteArray) {
            var pos = 0
            for (x in 0 until width) {
                for (y in height - 1 downTo 0) {
                    dst[pos++] = src[y * width + x]
                }
            }
            val uvHeight = height / 2
            var x = 0
            while (x < width) {
                for (y in uvHeight - 1 downTo 0) {
                    val srcIndex = width * height + y * width + x
                    dst[pos++] = src[srcIndex]
                    dst[pos++] = src[srcIndex + 1]
                }
                x += 2
            }
        }

        private fun rotateNv21By180(src: ByteArray, width: Int, height: Int, dst: ByteArray) {
            val frameSize = width * height
            for (i in 0 until frameSize) {
                dst[frameSize - 1 - i] = src[i]
            }
            val uvSize = frameSize / 2
            var i = 0
            while (i < uvSize) {
                dst[frameSize + uvSize - 2 - i] = src[frameSize + i]
                dst[frameSize + uvSize - 1 - i] = src[frameSize + i + 1]
                i += 2
            }
        }

        private fun rotateNv21By270(src: ByteArray, width: Int, height: Int, dst: ByteArray) {
            var pos = 0
            for (x in width - 1 downTo 0) {
                for (y in 0 until height) {
                    dst[pos++] = src[y * width + x]
                }
            }
            val uvHeight = height / 2
            var x = width - 2
            while (x >= 0) {
                for (y in 0 until uvHeight) {
                    val srcIndex = width * height + y * width + x
                    dst[pos++] = src[srcIndex]
                    dst[pos++] = src[srcIndex + 1]
                }
                x -= 2
            }
        }
    }
}
