package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolClientTest {
    @Test
    fun revokedAndInvalidTokensDoNotRetryAsNetworkDrops() {
        assertTrue(ProtocolClient.isPermanentAccessClose(4401))
        assertTrue(ProtocolClient.isPermanentAccessClose(4403))
        assertFalse(ProtocolClient.isPermanentAccessClose(1011))
        assertFalse(ProtocolClient.isPermanentAccessClose(1006))
    }

    @Test
    fun wireLanguageMapsBcp47TagsToContractCodes() {
        assertEquals("en", ProtocolClient.wireLanguage("en-IN"))
        assertEquals("en", ProtocolClient.wireLanguage("en"))
        assertEquals("hi", ProtocolClient.wireLanguage("hi-IN"))
        assertEquals("hi", ProtocolClient.wireLanguage("hi"))
    }

    @Test
    fun settleBudgetCoversCpuRemoteInferenceWithoutImmediateReconnect() {
        // GCP CPU remote YOLO uses up to ~9s; a 2.5s client settle was cancelling healthy sockets.
        assertTrue(ProtocolClient.FRAME_SETTLE_TIMEOUT_MS >= 9_000L)
        assertEquals(2, ProtocolClient.SETTLE_TIMEOUTS_BEFORE_RECONNECT)
        assertEquals(ProtocolClient.FRAME_SETTLE_TIMEOUT_MS, ProtocolClient.LOOK_TIMEOUT_MS)
    }

    @Test
    fun speakBudgetCoversCpuRemoteAlertAgeWithNetworkHeadroom() {
        // Server CPU remote ALERT_MAX_AGE_MS is 8500; phone age includes uplink RTT.
        assertEquals(9_000L, ProtocolClient.STALE_ALERT_MS)
        assertEquals(9_000L, ProtocolClient.LOOK_FRESHNESS_MS)
        assertEquals(9_000L, ProtocolClient.URGENT_FRESHNESS_MS)
        assertTrue(ProtocolClient.FRAME_SETTLE_TIMEOUT_MS > ProtocolClient.STALE_ALERT_MS)
        assertTrue(ProtocolClient.STALE_ALERT_MS >= 8_500L)
    }

    @Test
    fun streamGateRequiresReadyAndLiveVision() {
        assertFalse(ProtocolClient.streamEnabled(sessionReady = false, visionEnabled = false))
        assertFalse(ProtocolClient.streamEnabled(sessionReady = true, visionEnabled = false))
        assertFalse(ProtocolClient.streamEnabled(sessionReady = false, visionEnabled = true))
        assertTrue(ProtocolClient.streamEnabled(sessionReady = true, visionEnabled = true))
    }

    @Test
    fun transportFailureStateDistinguishesAuthenticationFromNetworkFailure() {
        assertEquals("authentication", ProtocolClient.transportFailureClass(401))
        assertEquals("authentication", ProtocolClient.transportFailureClass(403))
        assertEquals("http", ProtocolClient.transportFailureClass(503))
        assertEquals("transport", ProtocolClient.transportFailureClass(null))
    }
}
