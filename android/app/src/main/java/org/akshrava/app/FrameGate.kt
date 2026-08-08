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
    private const val OCCLUDED_LUMA = 8               // mean luma below this = lens covered / black buffer
    private const val GLARE_MEAN_LUMA = 230           // mean luma above this *and* flat = washed out
    private const val GLARE_MAX_VARIANCE = 40.0       // nearly uniform bright field

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

    /** Mean luma of the thumbnail, or -1 when there is nothing to measure. */
    fun meanLuma(current: IntArray): Int {
        if (current.isEmpty()) return -1
        var sum = 0L
        for (v in current) sum += v
        return (sum / current.size).toInt()
    }

    /**
     * Mean luma under [OCCLUDED_LUMA] ⇒ covered lens, pocket, or OEM black analysis buffers (F-02).
     *
     * The threshold is deliberately conservative. Raising it makes the guard fire on scenes that
     * are merely dim — dusk, an unlit corridor, a shaded underpass — and a false occlusion verdict
     * does not degrade gracefully: the frame is dropped, so assistance simply stops in exactly the
     * low-light conditions where the user has least other information. Only frames that are
     * essentially black qualify.
     *
     * Takes the mean the caller already computed: occlusion, glare and the debug log every needed
     * it, and each recomputing its own meant three passes over the thumbnail on every frame.
     */
    fun isOccluded(meanLuma: Int): Boolean = meanLuma < OCCLUDED_LUMA

    fun isOccluded(current: IntArray): Boolean =
        if (current.isEmpty()) true else isOccluded(meanLuma(current))

    /**
     * High mean luma with very low spatial variance ⇒ sun / specular washout (F-42).
     *
     * Distinct from blur (which is Laplacian energy): a glared frame is bright *and* nearly flat,
     * so YOLO returns empty while the user thinks the camera is fine. Thresholds are deliberately
     * high so dusk streets and indoor fluorescents do not false-trigger.
     */
    fun isGlared(current: IntArray, meanLuma: Int): Boolean {
        if (current.isEmpty()) return false
        if (meanLuma < GLARE_MEAN_LUMA) return false
        var varAcc = 0.0
        for (v in current) {
            val d = v - meanLuma
            varAcc += d * d
        }
        val variance = varAcc / current.size
        return variance < GLARE_MAX_VARIANCE
    }

    fun isGlared(current: IntArray): Boolean = isGlared(current, meanLuma(current))

    /** Consecutive blurred frames before the wipe-lens prompt is worth saying (F-72). */
    const val BLUR_FRAMES_BEFORE_ANNOUNCE = 5

    /** Gap between repeats of the wipe-lens prompt. */
    const val BLUR_ANNOUNCE_COOLDOWN_MS = 60_000L

    /**
     * Whether the wipe-lens prompt may speak now (F-72).
     *
     * Blur never drops a frame, so this gates speech only. The evidence bar stays at several
     * consecutive frames because one smeared frame is ordinary on a bouncing lanyard, and a
     * prompt for it teaches the user to ignore the prompt that matters.
     *
     * [lastAnnounceMs] is null when nothing has been said yet this session, rather than 0. These
     * timestamps are `SystemClock.elapsedRealtime()` — time since boot — so a 0 sentinel reads as
     * "announced at boot" and swallows the first prompt entirely on a phone that started
     * assistance within the cooldown of powering on.
     */
    fun shouldAnnounceBlur(nowMs: Long, consecutiveBlurredFrames: Int, lastAnnounceMs: Long?): Boolean {
        if (consecutiveBlurredFrames < BLUR_FRAMES_BEFORE_ANNOUNCE) return false
        val last = lastAnnounceMs ?: return true
        return nowMs - last >= BLUR_ANNOUNCE_COOLDOWN_MS
    }
}
