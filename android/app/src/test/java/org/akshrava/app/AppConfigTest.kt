package org.akshrava.app

import android.content.Context
import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.ArgumentMatchers.anyBoolean
import org.mockito.ArgumentMatchers.anyInt
import org.mockito.ArgumentMatchers.anyString
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class AppConfigTest {

    private lateinit var mockContext: Context
    private lateinit var mockPrefs: SharedPreferences
    private lateinit var mockEditor: SharedPreferences.Editor

    @Before
    fun setUp() {
        mockContext = mock(Context::class.java)
        mockPrefs = mock(SharedPreferences::class.java)
        mockEditor = mock(SharedPreferences.Editor::class.java)

        `when`(mockContext.getSharedPreferences(anyString(), anyInt())).thenReturn(mockPrefs)
        `when`(mockPrefs.edit()).thenReturn(mockEditor)
        `when`(mockEditor.putString(anyString(), anyString())).thenReturn(mockEditor)
        `when`(mockEditor.putBoolean(anyString(), anyBoolean())).thenReturn(mockEditor)
        `when`(mockEditor.remove(anyString())).thenReturn(mockEditor)
        `when`(mockEditor.commit()).thenReturn(true)
    }

    @Test
    fun loadReturnsDefaultValuesWhenPreferencesEmpty() {
        `when`(mockPrefs.getString("endpoint", BuildConfig.DEFAULT_WSS_ENDPOINT)).thenReturn(BuildConfig.DEFAULT_WSS_ENDPOINT)
        `when`(mockPrefs.getString("language", "en-IN")).thenReturn("en-IN")
        `when`(mockPrefs.getString("calibration", "unprovisioned")).thenReturn("unprovisioned")
        `when`(mockPrefs.getBoolean("debug_telemetry", false)).thenReturn(false)

        val config = AppConfigStore.load(mockContext)
        assertEquals(BuildConfig.DEFAULT_WSS_ENDPOINT, config.endpoint)
        assertEquals("en-IN", config.language)
        assertEquals("unprovisioned", config.calibrationId)
        assertFalse(config.debugTelemetry)
    }

    @Test
    fun saveWritesConfigurationToSharedPreferences() {
        val config = AppConfig(
            endpoint = "wss://test.example.com/v1/session",
            deviceToken = "",
            language = "hi-IN",
            calibrationId = "test-calib-1",
            debugTelemetry = true
        )

        val result = AppConfigStore.save(mockContext, config)
        assertTrue(result)
    }

    @Test
    fun requiredProvisioningRejectsTheDefaultCalibrationSentinel() {
        val base = AppConfig(
            endpoint = "wss://test.example.com/v1/session",
            deviceToken = "token",
            language = "en-IN",
            calibrationId = "unprovisioned",
            debugTelemetry = false
        )
        assertFalse(base.hasRequiredProvisioning())
        assertFalse(base.copy(deviceToken = "").hasRequiredProvisioning())
        assertFalse(base.copy(endpoint = "").hasRequiredProvisioning())
        assertTrue(base.copy(calibrationId = "test-r0").hasRequiredProvisioning())
    }
}
