package org.akshrava.app

import androidx.camera.core.ImageProxy
import kotlin.math.abs

/**
 * Two cheap pre-encode filters (§3.2) computed on a 32x32 luma thumbnail read straight
 * from the camera Y plane: drop near-duplicate frames (nothing changed) and drop
 * motion-smeared frames (a bouncing lanyard on a cheap sensor). Only clearly duplicate frames
 * are dropped. Blur is exposed as a diagnostic metric, never a safety decision: the cost of a
 * wrong blur estimate is a missed look.
 */
object FrameGate {
    private const val GRID = 32
    private const val DUPLICATE_MAD = 6            // mean abs luma diff below this = duplicate
    private const val BLUR_LAPLACIAN_VARIANCE = 12.0  // below this on the thumbnail = smeared

    /** Sub-samples the Y plane into a GRID x GRID grayscale grid without allocating a bitmap. */
    fun luma(image: ImageProxy): IntArray {
        val plane = image.planes[0]
        val buffer = plane.buffer.duplicate()
        val base = buffer.position()
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val width = image.width
        val height = image.height
        val out = IntArray(GRID * GRID)
        for (gy in 0 until GRID) {
            val sy = (gy * height) / GRID
            for (gx in 0 until GRID) {
                val sx = (gx * width) / GRID
                out[gy * GRID + gx] = buffer.get(base + sy * rowStride + sx * pixelStride).toInt() and 0xFF
            }
        }
        return out
    }

    fun isDuplicate(previous: IntArray?, current: IntArray): Boolean {
        if (previous == null || previous.size != current.size) return false
        var sum = 0L
        for (i in current.indices) sum += abs(current[i] - previous[i])
        return sum / current.size < DUPLICATE_MAD
    }

    fun isBlurred(current: IntArray): Boolean {
        var mean = 0.0
        for (gy in 1 until GRID - 1) {
            for (gx in 1 until GRID - 1) {
                val i = gy * GRID + gx
                val value = (4 * current[i] - current[i - 1] - current[i + 1] - current[i - GRID] - current[i + GRID]).toDouble()
                mean += value
            }
        }
        val samples = (GRID - 2) * (GRID - 2)
        mean /= samples
        var variance = 0.0
        for (gy in 1 until GRID - 1) {
            for (gx in 1 until GRID - 1) {
                val i = gy * GRID + gx
                val value = (4 * current[i] - current[i - 1] - current[i + 1] - current[i - GRID] - current[i + GRID]).toDouble()
                val delta = value - mean
                variance += delta * delta
            }
        }
        variance /= samples
        return variance < BLUR_LAPLACIAN_VARIANCE
    }

    /** Mean luma under ~8/255 ⇒ covered lens, pocket, or OEM black analysis buffers. */
    fun isNearBlack(current: IntArray): Boolean {
        if (current.isEmpty()) return true
        var sum = 0L
        for (v in current) sum += v
        return sum / current.size < 8
    }
}
