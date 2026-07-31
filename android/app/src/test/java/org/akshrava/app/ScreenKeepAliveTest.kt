package org.akshrava.app

import android.content.Context
import android.os.PowerManager
import android.view.WindowManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.mockito.ArgumentMatchers.anyInt
import org.mockito.ArgumentMatchers.anyString
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class ScreenKeepAliveTest {

    @Test
    fun startWithWakeLockFallback() {
        val mockContext = mock(Context::class.java)
        val mockPm = mock(PowerManager::class.java)
        val mockWl = mock(PowerManager.WakeLock::class.java)
        val mockWm = mock(WindowManager::class.java)

        `when`(mockContext.getSystemService(WindowManager::class.java)).thenReturn(mockWm)
        `when`(mockContext.getSystemService(PowerManager::class.java)).thenReturn(mockPm)
        @Suppress("DEPRECATION")
        `when`(mockPm.newWakeLock(anyInt(), anyString())).thenReturn(mockWl)

        val keepAlive = ScreenKeepAlive(mockContext)
        assertFalse(keepAlive.isHoldingScreenOn())
        assertEquals(ScreenKeepAlive.Mode.NONE, keepAlive.mode)

        keepAlive.start()
        keepAlive.renew()
        keepAlive.stop()

        assertEquals(ScreenKeepAlive.Mode.NONE, keepAlive.mode)
        assertFalse(keepAlive.isHoldingScreenOn())
    }
}
