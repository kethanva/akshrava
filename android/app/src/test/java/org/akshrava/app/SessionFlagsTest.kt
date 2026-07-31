package org.akshrava.app

import android.content.Context
import android.content.SharedPreferences
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.mockito.ArgumentMatchers.anyBoolean
import org.mockito.ArgumentMatchers.anyInt
import org.mockito.ArgumentMatchers.anyLong
import org.mockito.ArgumentMatchers.anyString
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class SessionFlagsTest {

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
        `when`(mockEditor.putBoolean(anyString(), anyBoolean())).thenReturn(mockEditor)
        `when`(mockEditor.putLong(anyString(), anyLong())).thenReturn(mockEditor)
    }

    @Test
    fun isActiveReturnsFalseByDefault() {
        `when`(mockPrefs.getBoolean("session_active", false)).thenReturn(false)
        assertFalse(SessionFlags.isActive(mockContext))
    }

    @Test
    fun isActiveReturnsTrueWhenActiveSet() {
        `when`(mockPrefs.getBoolean("session_active", false)).thenReturn(true)
        assertTrue(SessionFlags.isActive(mockContext))
    }

    @Test
    fun isStaleReturnsTrueWhenHeartbeatOlderThan3Minutes() {
        // In default Android test stub, SystemClock.elapsedRealtime() returns 0L.
        // Setting heartbeat_ms to -200,000L means elapsed - heartbeat = 200,000L > 180,000L.
        `when`(mockPrefs.getLong("heartbeat_ms", 0L)).thenReturn(-200_000L)
        assertTrue(SessionFlags.isStale(mockContext))
    }

    @Test
    fun isStaleReturnsFalseWhenHeartbeatIsRecent() {
        `when`(mockPrefs.getLong("heartbeat_ms", 0L)).thenReturn(0L)
        assertFalse(SessionFlags.isStale(mockContext))
    }
}
