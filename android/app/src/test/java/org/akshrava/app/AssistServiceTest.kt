package org.akshrava.app

import android.content.Intent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class AssistServiceTest {

    @Test
    fun constantsMatchSafetyAndOperationalBoundaries() {
        assertEquals("org.akshrava.app.START", AssistService.ACTION_START)
        assertEquals("org.akshrava.app.STOP", AssistService.ACTION_STOP)
        assertEquals(3, AssistService.OCCLUDED_FRAMES_BEFORE_ANNOUNCE)
        assertEquals(30_000L, AssistService.HEARTBEAT_INTERVAL_MS)
        assertEquals(15_000L, AssistService.CAMERA_STALL_REBIND_MS)
        assertEquals(15_000L, AssistService.FRAME_SLOT_WEDGED_MS)
        assertTrue(AssistService.WAKE_LOCK_TIMEOUT_MS >= 3600_000L)
    }

    // ---- camera stall recovery (AssistService.shouldRebindForStall) ----
    //
    // The analyzer going silent while the session is meant to be live ends assistance with the
    // socket still open and nothing in any log to explain it, so the rebind rule is worth pinning
    // independently of a camera or a Handler.

    @Test
    fun `no rebind while the analyzer is still delivering frames`() {
        val now = 100_000L
        assertFalse(AssistService.shouldRebindForStall(now, now))
        assertFalse(AssistService.shouldRebindForStall(now, now - 1_000L))
        assertFalse(AssistService.shouldRebindForStall(now, now - 14_999L))
    }

    @Test
    fun `no rebind exactly at the threshold, only strictly past it`() {
        val now = 100_000L
        val atThreshold = now - AssistService.CAMERA_STALL_REBIND_MS
        assertFalse(AssistService.shouldRebindForStall(now, atThreshold))
        assertTrue(AssistService.shouldRebindForStall(now, atThreshold - 1L))
    }

    @Test
    fun `rebind once the analyzer has been silent past the stall window`() {
        val now = 1_000_000L
        assertTrue(AssistService.shouldRebindForStall(now, now - 20_000L))
        // A long silence (device slept, camera HAL wedged) still rebinds rather than overflowing
        // into some other branch.
        assertTrue(AssistService.shouldRebindForStall(now, now - 900_000L))
    }

    @Test
    fun `an unbound camera is quiet, not stalled`() {
        // 0 means "no successful bind yet" -- there is nothing to recover before the camera is
        // bound, and rebinding in a loop while a bind is still in flight would be harmful.
        assertFalse(AssistService.shouldRebindForStall(0L, 0L))
        assertFalse(AssistService.shouldRebindForStall(600_000L, 0L))
    }

    @Test
    fun `a bind that succeeds but never delivers a frame is recovered`() {
        // bindCamera() baselines lastAnalyzeAtMs on a successful bind precisely so this case is
        // covered. Previously the baseline was only set by the first frame, so an OEM HAL that
        // accepted the configuration and then emitted nothing armed nothing at all: camera bound,
        // socket open, no drop, no stall, and assistance simply over with no recovery path the
        // user could discover. That is the exact failure this detector exists for.
        val boundAt = 500_000L
        val silentSince = boundAt + AssistService.CAMERA_STALL_REBIND_MS + 1L
        assertTrue(AssistService.shouldRebindForStall(silentSince, boundAt))
        // ...but a bind that is merely still warming up is left alone.
        assertFalse(AssistService.shouldRebindForStall(boundAt + 2_000L, boundAt))
    }

    @Test
    fun `cross-thread frame pipeline fields are volatile`() {
        // Camera state is published by the main thread and consumed by the analyzer; timing state
        // also crosses the analyzer, OkHttp listener, reconnect scheduler, and main threads. These
        // references must not be cached across a Stop/Start generation boundary.
        for (name in listOf(
            "lastAnalyzeAtMs",
            "lastQualityRebindAtMs",
            "frameEncoder",
            "poseTracker",
            "alertManager"
        )) {
            val field = AssistService::class.java.getDeclaredField(name)
            assertTrue(
                "$name is shared across threads and must be @Volatile",
                java.lang.reflect.Modifier.isVolatile(field.modifiers)
            )
        }
    }

    @Test
    fun assistServiceInstantiableAndMockable() {
        val service = mock(AssistService::class.java)
        assertNotNull(service)

        val intent = Intent(AssistService.ACTION_STOP)
        `when`(service.onStartCommand(intent, 0, 1)).thenReturn(1)
        assertEquals(1, service.onStartCommand(intent, 0, 1))
    }
}
