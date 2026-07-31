package org.akshrava.app

import android.content.Context
import android.view.Display
import android.view.Surface
import android.view.WindowManager
import org.junit.Assert.assertEquals
import org.junit.Test
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class DisplayRotationTest {

    @Test
    fun currentDisplayRotationReturnsRotationFromWindowManagerFallback() {
        val mockContext = mock(Context::class.java)
        val mockWm = mock(WindowManager::class.java)
        val mockDisplay = mock(Display::class.java)

        `when`(mockContext.getSystemService(Context.WINDOW_SERVICE)).thenReturn(mockWm)
        @Suppress("DEPRECATION")
        `when`(mockWm.defaultDisplay).thenReturn(mockDisplay)
        `when`(mockDisplay.rotation).thenReturn(Surface.ROTATION_90)

        val rotation = mockContext.currentDisplayRotation()
        assertEquals(Surface.ROTATION_90, rotation)
    }
}
