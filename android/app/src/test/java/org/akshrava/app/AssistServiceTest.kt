package org.akshrava.app

import android.content.Intent
import org.junit.Assert.assertEquals
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

    @Test
    fun assistServiceInstantiableAndMockable() {
        val service = mock(AssistService::class.java)
        assertNotNull(service)

        val intent = Intent(AssistService.ACTION_STOP)
        `when`(service.onStartCommand(intent, 0, 1)).thenReturn(1)
        assertEquals(1, service.onStartCommand(intent, 0, 1))
    }
}
