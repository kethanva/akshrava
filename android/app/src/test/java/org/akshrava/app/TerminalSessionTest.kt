package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class TerminalSessionTest {
    @Test
    fun sessionTakenOverCloseIsTerminal() {
        assertTrue(ProtocolClient.isSessionTakenOverClose(4409))
        assertFalse(ProtocolClient.isPermanentAccessClose(4409))
    }

    @Test
    fun jsonSessionSupersededIsTerminalEvenWithoutCloseCode() {
        assertTrue(ProtocolClient.isTerminalErrorCode("session_superseded"))
        assertTrue(ProtocolClient.isTerminalErrorCode("device_revoked"))
        assertTrue(ProtocolClient.isTerminalErrorCode("authentication_failed"))
        assertFalse(ProtocolClient.isTerminalErrorCode("malformed_control_message"))
        assertFalse(ProtocolClient.isTerminalErrorCode("worker_saturated"))
        assertEquals("op_session_taken_over", ProtocolClient.speechKeyForTerminalError("session_superseded"))
        assertEquals("op_access_revoked", ProtocolClient.speechKeyForTerminalError("device_revoked"))
        assertEquals("op_auth_failed", ProtocolClient.speechKeyForTerminalError("authentication_failed"))
        assertEquals("op_access_revoked", ProtocolClient.speechKeyForHandshakeHttp(403))
        assertEquals("op_auth_failed", ProtocolClient.speechKeyForHandshakeHttp(401))
    }

    @Test
    fun capacityCloseIsNotTerminal() {
        assertFalse(ProtocolClient.isSessionTakenOverClose(1013))
        assertFalse(ProtocolClient.isPermanentAccessClose(1013))
        assertEquals("temporary_overload", ProtocolClient.closeClass(1013))
    }

    @Test
    fun terminalClientStopsTheHeartbeat() {
        // maybeHeartbeat first line: if (client?.isTerminal() == true) return
        // A terminal client must not keep Watchdog heartbeats alive.
        val source = sourceFile("AssistService.kt").readText()
        val heartbeat = Regex(
            """private fun maybeHeartbeat\(now: Long\) \{\s*if \(client\?\.isTerminal\(\) == true\) return""",
            setOf(RegexOption.DOT_MATCHES_ALL)
        )
        assertTrue(
            "isTerminal true means skip the heartbeat",
            heartbeat.containsMatchIn(source)
        )
    }

    @Test
    fun closeClassNamesTheSessionConflict() {
        assertEquals("session_conflict", ProtocolClient.closeClass(4409))
    }

    @Test
    fun permanentFailureDoesNotSpeakTwice() {
        // JSON device_revoked plus the following 4403 onClosed both call handlePermanentFailure.
        val source = sourceFile("ProtocolClient.kt").readText()
        val guard = Regex(
            """private fun handlePermanentFailure\(speechKey: String\) \{\s*if \(closedByUser\) return""",
            setOf(RegexOption.DOT_MATCHES_ALL)
        )
        assertTrue("already-terminal must not speak recovery twice", guard.containsMatchIn(source))
    }

    private fun sourceFile(name: String): File {
        val candidates = listOf(
            File("src/main/java/org/akshrava/app/$name"),
            File("app/src/main/java/org/akshrava/app/$name"),
            File("android/app/src/main/java/org/akshrava/app/$name")
        )
        return candidates.first { it.isFile }
    }
}
