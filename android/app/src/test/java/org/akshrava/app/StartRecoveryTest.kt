package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Start-press recovery semantics.
 *
 * Duplicate START on a healthy session must be ignored (re-taps and OEM intent redelivery were
 * tearing down a live WSS mid-walk — the "assistance died then came back" flap). But the guard
 * that does this keys off `client != null`, and a terminally-dead client is still non-null:
 * ProtocolClient.handlePermanentFailure (close 4401/4403, HTTP 401/403) stops the reconnect
 * executor and can never come back. Ignoring Start in that state would strand the user with no
 * recovery at all, since pressing Start is the only route back. These tests pin the distinction.
 */
class StartRecoveryTest {
    /** Mirrors the guard in AssistService.startAssistance. */
    private fun ignoresDuplicateStart(
        stopping: Boolean,
        hasClient: Boolean,
        hasAlertManager: Boolean,
        clientTerminal: Boolean
    ): Boolean {
        val recoverable = !(hasClient && clientTerminal)
        return !stopping && recoverable && hasClient && hasAlertManager
    }

    @Test
    fun duplicateStartOnHealthySessionIsIgnored() {
        assertTrue(
            "a re-tap on a live session must not tear down the WSS",
            ignoresDuplicateStart(
                stopping = false, hasClient = true, hasAlertManager = true, clientTerminal = false
            )
        )
    }

    @Test
    fun startRebuildsATerminallyDeadClient() {
        // Device revoked / token rejected: reconnect executor is shut down, so waiting never
        // helps. Start must fall through to the rebuild path.
        assertFalse(
            "Start must rebuild when the client can never recover on its own",
            ignoresDuplicateStart(
                stopping = false, hasClient = true, hasAlertManager = true, clientTerminal = true
            )
        )
    }

    @Test
    fun startRebuildsHalfDeadSessions() {
        // Camera-failure teardown nulls the client but leaves AlertManager; Start is the
        // documented recovery action and must not be swallowed.
        assertFalse(
            ignoresDuplicateStart(
                stopping = false, hasClient = false, hasAlertManager = true, clientTerminal = false
            )
        )
    }

    @Test
    fun startInterruptsAnInProgressStop() {
        assertFalse(
            "Start during teardown must rebuild, not no-op",
            ignoresDuplicateStart(
                stopping = true, hasClient = true, hasAlertManager = true, clientTerminal = false
            )
        )
    }
}
