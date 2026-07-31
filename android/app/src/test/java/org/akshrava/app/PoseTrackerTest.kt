package org.akshrava.app

import android.content.Context
import android.hardware.SensorManager
import android.view.Display
import android.view.Surface
import android.view.WindowManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class PoseTrackerTest {
    @Test
    fun extremePitchUsesCentidegreeThreshold() {
        assertFalse(PoseTracker.isExtremePitch(null))
        assertFalse(PoseTracker.isExtremePitch(0))
        assertFalse(PoseTracker.isExtremePitch(PoseTracker.EXTREME_PITCH_CDEG - 1))
        assertTrue(PoseTracker.isExtremePitch(PoseTracker.EXTREME_PITCH_CDEG))
        assertTrue(PoseTracker.isExtremePitch(-PoseTracker.EXTREME_PITCH_CDEG))
    }

    @Test
    fun extremeSinceTracksStreakAndClearsOnNormalPitch() {
        assertNull(PoseTracker.extremeSinceUpdated(1_000L, pitchCdeg = 0, extremeSinceMs = null))
        val started = PoseTracker.extremeSinceUpdated(1_000L, pitchCdeg = 6_000, extremeSinceMs = null)
        assertEquals(1_000L, started)
        assertEquals(1_000L, PoseTracker.extremeSinceUpdated(1_500L, pitchCdeg = 6_000, extremeSinceMs = started))
        assertNull(PoseTracker.extremeSinceUpdated(2_000L, pitchCdeg = 0, extremeSinceMs = started))
    }

    @Test
    fun tiltAnnounceRequiresHoldAndCooldown() {
        val since = 1_000L
        assertFalse(PoseTracker.shouldAnnounceTilt(nowMs = 1_500L, extremeSinceMs = since, lastAnnounceMs = 0L))
        assertTrue(PoseTracker.shouldAnnounceTilt(nowMs = 3_000L, extremeSinceMs = since, lastAnnounceMs = 0L))
        assertFalse(
            PoseTracker.shouldAnnounceTilt(
                nowMs = 5_000L,
                extremeSinceMs = since,
                lastAnnounceMs = 3_000L,
                cooldownMs = 8_000L
            )
        )
        assertTrue(
            PoseTracker.shouldAnnounceTilt(
                nowMs = 11_000L,
                extremeSinceMs = since,
                lastAnnounceMs = 3_000L,
                cooldownMs = 8_000L
            )
        )
        assertFalse(PoseTracker.shouldAnnounceTilt(nowMs = 11_000L, extremeSinceMs = null, lastAnnounceMs = 0L))
    }

    @Test
    fun instanceMethodsExecuteCleanlyWithMockedSensorManager() {
        val mockContext = mock(Context::class.java)
        val mockSm = mock(SensorManager::class.java)
        val mockWm = mock(WindowManager::class.java)
        val mockDisplay = mock(Display::class.java)

        `when`(mockContext.getSystemService(Context.SENSOR_SERVICE)).thenReturn(mockSm)
        `when`(mockContext.getSystemService(Context.WINDOW_SERVICE)).thenReturn(mockWm)
        @Suppress("DEPRECATION")
        `when`(mockWm.defaultDisplay).thenReturn(mockDisplay)
        `when`(mockDisplay.rotation).thenReturn(Surface.ROTATION_0)

        val tracker = PoseTracker(mockContext)
        assertNotNull(tracker)

        tracker.start()
        val snapshot = tracker.snapshot()
        assertNotNull(snapshot)
        val motionState = tracker.motionState()
        assertNotNull(motionState)
        tracker.stop()
    }
}
