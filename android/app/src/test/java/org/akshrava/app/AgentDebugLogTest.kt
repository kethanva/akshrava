package org.akshrava.app

import android.content.Context
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock
import java.io.File

class AgentDebugLogTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    private lateinit var mockContext: Context
    private lateinit var mockAppContext: Context
    private lateinit var filesDir: File

    @Before
    fun setUp() {
        mockContext = mock(Context::class.java)
        mockAppContext = mock(Context::class.java)
        filesDir = tempFolder.newFolder("files")
        `when`(mockContext.applicationContext).thenReturn(mockAppContext)
        `when`(mockAppContext.filesDir).thenReturn(filesDir)
    }

    @Test
    fun bindDisabledDoesNotWriteFile() {
        AgentDebugLog.bind(mockContext, debugEnabled = false)
        AgentDebugLog.log("hyp1", "loc1", "msg1")

        Thread.sleep(100) // Allow executor to process if any
        val file = File(filesDir, "agent-debug.ndjson")
        assertFalse(file.exists())
    }

    @Test
    fun bindEnabledWritesNdjsonFile() {
        AgentDebugLog.bind(mockContext, debugEnabled = true)
        AgentDebugLog.log("hyp1", "loc1", "msg1", mapOf("key" to "val"))

        Thread.sleep(200) // Allow async executor to complete write
        val file = File(filesDir, "agent-debug.ndjson")
        assertTrue(file.exists())
        val content = file.readText()
        assertTrue(content.contains("hyp1"))
        assertTrue(content.contains("loc1"))
        assertTrue(content.contains("msg1"))
        assertTrue(content.contains("key"))
    }

    @Test
    fun fileSizeBoundaryDeletesFileWhenExceeding512KB() {
        AgentDebugLog.bind(mockContext, debugEnabled = true)
        val file = File(filesDir, "agent-debug.ndjson")
        // Create dummy file >= 512KB
        val dummyData = ByteArray(513 * 1024) { 'a'.code.toByte() }
        file.writeBytes(dummyData)
        assertTrue(file.length() >= 512 * 1024L)

        AgentDebugLog.log("hyp2", "loc2", "msg2")
        Thread.sleep(200)

        assertTrue(file.exists())
        assertTrue(file.length() < 512 * 1024L)
        val content = file.readText()
        assertTrue(content.contains("hyp2"))
    }
}
