package org.akshrava.app

import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.mock

class HeadsetControlsTest {

    @Test
    fun headsetControlsMockableAndInvocable() {
        val mockControls = mock(HeadsetControls::class.java)
        assertNotNull(mockControls)
        mockControls.start()
        mockControls.stop()
    }
}
