package org.akshrava.app

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.os.BatteryManager
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class DeviceCapabilityTest {

    @Test
    fun isEmulatorSafelyExecutes() {
        runCatching { DeviceCapability.isEmulator() }
    }

    @Test
    fun isConstrainedWithNullActivityManagerReturnsFalse() {
        val mockContext = mock(Context::class.java)
        `when`(mockContext.getSystemService(ActivityManager::class.java)).thenReturn(null)
        assertFalse(DeviceCapability.isConstrained(mockContext))
    }

    @Test
    fun isConstrainedWithLowRamDeviceReturnsTrue() {
        val mockContext = mock(Context::class.java)
        val mockAm = mock(ActivityManager::class.java)
        `when`(mockContext.getSystemService(ActivityManager::class.java)).thenReturn(mockAm)
        `when`(mockAm.isLowRamDevice).thenReturn(true)

        assertTrue(DeviceCapability.isConstrained(mockContext))
    }

    @Test
    fun initialQualityAndAnalysisSideCapForConstrainedDevice() {
        val mockContext = mock(Context::class.java)
        val mockAm = mock(ActivityManager::class.java)
        `when`(mockContext.getSystemService(ActivityManager::class.java)).thenReturn(mockAm)
        `when`(mockAm.isLowRamDevice).thenReturn(true)

        val quality = DeviceCapability.initialQuality(mockContext)
        assertNotNull(quality)
        assertEquals(480, DeviceCapability.analysisSideCap(mockContext))
        assertTrue(DeviceCapability.preferConservativeSettle(mockContext))
    }

    @Test
    fun batteryPercentValidIntentComputesPercentage() {
        val intent = mock(Intent::class.java)
        `when`(intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)).thenReturn(45)
        `when`(intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)).thenReturn(100)

        assertEquals(45, DeviceCapability.batteryPercent(intent))
    }

    @Test
    fun batteryPercentInvalidIntentReturnsNull() {
        assertNull(DeviceCapability.batteryPercent(null))

        val intent = mock(Intent::class.java)
        `when`(intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)).thenReturn(-1)
        `when`(intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1)).thenReturn(100)
        assertNull(DeviceCapability.batteryPercent(intent))
    }

    @Test
    fun batteryStatusTextNullPctReturnsUnavailable() {
        assertEquals("Battery level unavailable.", DeviceCapability.batteryStatusText(null))
    }

    @Test
    fun batteryStatusTextValidPctFormatsSpokenText() {
        val text80 = DeviceCapability.batteryStatusText(80)
        assertTrue(text80.contains("80 percent"))

        val text2 = DeviceCapability.batteryStatusText(2)
        assertTrue(text2.contains("2 percent"))
        assertTrue(text2.contains("less than an hour"))
    }
}
