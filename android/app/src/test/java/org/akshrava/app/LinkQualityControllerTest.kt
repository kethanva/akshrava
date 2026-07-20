package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LinkQualityControllerTest {
    @Test
    fun serverHintIsHonoredUntilLinkStressShedsFurther() {
        val link = LinkQualityController()
        val first = link.onServerQuality(Quality(maxSide = 640, jpegQ = 55, fps = 1.0))
        assertEquals(640, first.maxSide)
        assertEquals(55, first.jpegQ)

        link.onRoundTrip(1_500L)
        val stressed = link.onRoundTrip(1_600L)
        assertTrue(stressed.maxSide <= 512)
        assertTrue(stressed.jpegQ <= 48)
        assertTrue(stressed.fps <= 0.85)
    }

    @Test
    fun settleTimeoutRaisesStressToward3gFloor() {
        val link = LinkQualityController()
        link.onServerQuality(Quality(maxSide = 640, jpegQ = 55, fps = 1.0))
        link.onSettleTimeout()
        link.onSettleTimeout()
        val floor = link.onSettleTimeout()
        assertEquals(320, floor.maxSide)
        assertEquals(28, floor.jpegQ)
        assertEquals(0.35, floor.fps, 0.001)
        assertEquals(3, link.stressLevel())
    }

    @Test
    fun neverRaisesAboveServerHint() {
        val link = LinkQualityController()
        link.onServerQuality(Quality(maxSide = 384, jpegQ = 32, fps = 0.45))
        // Healthy RTTs recover stress but must not exceed the server ceiling.
        repeat(5) { link.onRoundTrip(200L) }
        val effective = link.effectiveQuality()
        assertEquals(384, effective.maxSide)
        assertEquals(32, effective.jpegQ)
        assertEquals(0.45, effective.fps, 0.001)
    }

    @Test
    fun qualityFromServerAccepts3gLadderFloors() {
        val q = Quality.fromServer(320, 28, 0.35)
        assertEquals(320, q.maxSide)
        assertEquals(28, q.jpegQ)
        assertEquals(0.35, q.fps, 0.001)
    }

    @Test
    fun moreConservativePicksCheaperOfEachAxis() {
        val a = Quality(maxSide = 640, jpegQ = 40, fps = 0.5)
        val b = Quality(maxSide = 384, jpegQ = 60, fps = 1.0)
        val c = a.moreConservative(b)
        assertEquals(384, c.maxSide)
        assertEquals(40, c.jpegQ)
        assertEquals(0.5, c.fps, 0.001)
    }
}
