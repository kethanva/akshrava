package org.akshrava.app

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.graphics.Rect
import android.graphics.YuvImage
import androidx.camera.core.ImageProxy
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import kotlin.math.roundToInt

data class EncodedFrame(val jpeg: ByteArray, val width: Int, val height: Int)

/** CPU-only conversion used at <=2 FPS. It favours correct rotation and predictable JPEG over speed. */
object FrameEncoder {
    // Analysis is single-threaded, but guard this reusable scratch buffer for any future caller.
    private var nv21Scratch = ByteArray(0)
    fun encode(image: ImageProxy, maxSide: Int, quality: Int): EncodedFrame {
        val nv21 = toNv21(image)
        val rotation = image.imageInfo.rotationDegrees
        // Most mounted phones already deliver the expected orientation. Avoid bitmap decode,
        // rotation and a second JPEG pass in that common case.
        if (rotation == 0 && maxOf(image.width, image.height) <= maxSide) {
            return EncodedFrame(
                compress(nv21, image.width, image.height, quality), image.width, image.height
            )
        }

        val raw = ByteArrayOutputStream(image.width * image.height / 3)
        YuvImage(nv21, android.graphics.ImageFormat.NV21, image.width, image.height, null)
            .compressToJpeg(Rect(0, 0, image.width, image.height), 95, raw)
        val bitmap = BitmapFactory.decodeByteArray(raw.toByteArray(), 0, raw.size())
        val rotated = if (rotation == 0) bitmap else Bitmap.createBitmap(
            bitmap, 0, 0, bitmap.width, bitmap.height, Matrix().apply { postRotate(rotation.toFloat()) }, true
        )
        if (rotated !== bitmap) bitmap.recycle()
        val scale = maxSide.toFloat() / maxOf(rotated.width, rotated.height).toFloat()
        val resized = if (scale < 1f) Bitmap.createScaledBitmap(
            rotated, (rotated.width * scale).roundToInt().coerceAtLeast(1),
            (rotated.height * scale).roundToInt().coerceAtLeast(1), true
        ) else rotated
        if (resized !== rotated) rotated.recycle()
        val output = ByteArrayOutputStream(resized.width * resized.height / 3)
        resized.compress(Bitmap.CompressFormat.JPEG, quality.coerceIn(25, 95), output)
        val result = EncodedFrame(output.toByteArray(), resized.width, resized.height)
        resized.recycle()
        return result
    }

    private fun compress(nv21: ByteArray, width: Int, height: Int, quality: Int): ByteArray {
        val output = ByteArrayOutputStream(width * height / 3)
        YuvImage(nv21, android.graphics.ImageFormat.NV21, width, height, null)
            .compressToJpeg(Rect(0, 0, width, height), quality.coerceIn(35, 70), output)
        return output.toByteArray()
    }

    @Synchronized
    private fun toNv21(image: ImageProxy): ByteArray {
        val width = image.width
        val height = image.height
        val required = width * height * 3 / 2
        if (nv21Scratch.size < required) nv21Scratch = ByteArray(required)
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

    private fun copyPlane(
        buffer: ByteBuffer, rowStride: Int, pixelStride: Int, width: Int, height: Int,
        out: ByteArray, offset: Int, outputStride: Int
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
}
