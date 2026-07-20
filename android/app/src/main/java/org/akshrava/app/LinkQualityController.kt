package org.akshrava.app

/**
 * Merges server `quality` hints with observed round-trip / settle-timeout stress.
 *
 * Server guidance remains authoritative for inference pressure; this controller only sheds
 * additional capture cost when the phone itself sees slow settles (typical on 3G/4G or when
 * CPU remote YOLO outlasts a prior quality step). Pure logic so AssistService stays thin.
 */
class LinkQualityController {
    companion object {
        private const val HIGH_RTT_MS = 1_200.0
        private const val RECOVER_RTT_MS = 700.0
        private const val MAX_STRESS = 3

        /** Progressive floors applied on top of the latest server hint (more conservative wins). */
        internal val STRESS_STEPS = listOf(
            Quality(maxSide = 512, jpegQ = 48, fps = 0.85),
            Quality(maxSide = 384, jpegQ = 35, fps = 0.55),
            Quality(maxSide = 320, jpegQ = 28, fps = 0.35),
        )
    }

    @Volatile
    private var serverQuality: Quality = Quality()

    @Volatile
    private var stress: Int = 0

    @Volatile
    private var ewmaRttMs: Double = 0.0

    @Volatile
    private var effective: Quality = Quality()

    fun effectiveQuality(): Quality = effective

    fun stressLevel(): Int = stress

    fun ewmaRttMs(): Double = ewmaRttMs

    @Synchronized
    fun onServerQuality(quality: Quality): Quality {
        serverQuality = quality
        return republish()
    }

    /** Successful frame settle round-trip; recovers stress when the link is healthy. */
    @Synchronized
    fun onRoundTrip(rttMs: Long): Quality {
        if (rttMs < 0L) return effective
        ewmaRttMs = if (ewmaRttMs <= 0.0) {
            rttMs.toDouble()
        } else {
            ewmaRttMs * 0.7 + rttMs.toDouble() * 0.3
        }
        when {
            ewmaRttMs >= HIGH_RTT_MS -> stress = (stress + 1).coerceAtMost(MAX_STRESS)
            ewmaRttMs <= RECOVER_RTT_MS && stress > 0 -> stress -= 1
        }
        return republish()
    }

    /** In-flight frame never settled in time — shed bytes before the next attempt. */
    @Synchronized
    fun onSettleTimeout(): Quality {
        stress = (stress + 1).coerceAtMost(MAX_STRESS)
        return republish()
    }

    private fun republish(): Quality {
        var merged = serverQuality
        if (stress > 0) {
            merged = merged.moreConservative(STRESS_STEPS[stress - 1])
        }
        effective = merged
        return merged
    }
}
