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
        assertEquals("ta", ProtocolClient.wireLanguage("ta-IN"))
        assertEquals("kn", ProtocolClient.wireLanguage("kn-IN"))
        assertEquals("ml", ProtocolClient.wireLanguage("ml-IN"))
        assertEquals("te", ProtocolClient.wireLanguage("te-IN"))
        assertEquals("en", ProtocolClient.wireLanguage("unknown"))
    }

    @Test
    fun settleBudgetCoversCpuRemoteInferenceWithoutImmediateReconnect() {
        // GCP CPU remote YOLO uses up to ~9s; a 2.5s client settle was cancelling healthy sockets.
        assertTrue(ProtocolClient.FRAME_SETTLE_TIMEOUT_MS >= 9_000L)
        assertEquals(2, ProtocolClient.SETTLE_TIMEOUTS_BEFORE_RECONNECT)
        assertEquals(ProtocolClient.FRAME_SETTLE_TIMEOUT_MS, ProtocolClient.LOOK_TIMEOUT_MS)
    }

    @Test
    fun speakBudgetPreservesSharedSafetyBoundary() {
        assertEquals(2_500L, ProtocolClient.STALE_ALERT_MS)
        assertEquals(2_500L, ProtocolClient.LOOK_FRESHNESS_MS)
        assertEquals(1_500L, ProtocolClient.URGENT_FRESHNESS_MS)
        assertTrue(ProtocolClient.FRAME_SETTLE_TIMEOUT_MS > ProtocolClient.STALE_ALERT_MS)
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

    @Test
    fun closeClassesAreStableAndDoNotNeedServerReasons() {
        assertEquals("normal", ProtocolClient.closeClass(1000))
        assertEquals("server_error", ProtocolClient.closeClass(1011))
        assertEquals("temporary_overload", ProtocolClient.closeClass(1013))
        assertEquals("authentication", ProtocolClient.closeClass(4401))
        assertEquals("other", ProtocolClient.closeClass(1006))
    }

    @Test
    fun softServerErrorsKeepTheSocketAndFreeTheInFlightSlot() {
        assertTrue(ProtocolClient.isSoftServerError("worker_saturated"))
        assertTrue(ProtocolClient.isSoftServerError("frame_rate_limited"))
        assertTrue(ProtocolClient.isSoftServerError("jpeg_dimension_mismatch"))
        assertTrue(ProtocolClient.isSoftServerError("non_monotonic_capture"))
        assertTrue(ProtocolClient.isSoftServerError("invalid_frame_header"))
        assertFalse(ProtocolClient.isSoftServerError("vision_unavailable"))
        assertFalse(ProtocolClient.isSoftServerError("protocol_violation"))
    }

    @Test
    fun poseCentidegreesClampToPhysicalWireRange() {
        assertEquals(-18_000, ProtocolClient.clampPoseCdeg(-20_000))
        assertEquals(18_000, ProtocolClient.clampPoseCdeg(19_000))
        assertEquals(-12_500, ProtocolClient.clampPoseCdeg(-12_500))
        assertEquals(ProtocolClient.POSE_CDEG_MIN, ProtocolClient.clampPoseCdeg(Int.MIN_VALUE))
        assertEquals(ProtocolClient.POSE_CDEG_MAX, ProtocolClient.clampPoseCdeg(Int.MAX_VALUE))
    }

    @Test
    fun wirePoseOmitsValuesThatWouldFatalCloseLegacyApi() {
        // Proven against live Cloud Run: roll_cdeg=-12500 still closes the socket today.
        assertEquals(null, ProtocolClient.wirePoseCdeg(-12_500))
        assertEquals(null, ProtocolClient.wirePoseCdeg(-9_001))
        assertEquals(-9_000, ProtocolClient.wirePoseCdeg(-9_000))
        assertEquals(-1_000, ProtocolClient.wirePoseCdeg(-1_000))
        assertEquals(1_800, ProtocolClient.wirePoseCdeg(1_800))
        assertEquals(18_000, ProtocolClient.wirePoseCdeg(20_000))
    }

    @Test
    fun wedgedSlotThresholdSitsAboveTheTimeoutMeantToPreventIt() {
        // AssistService flags a held frame slot as wedged only once the send-side settle timeout
        // has demonstrably failed to release it. If the threshold ever slipped below that
        // timeout, every ordinary slow inference would be reported as a dead session and the
        // signal would be worthless for finding the real one.
        assertTrue(
            "wedge threshold must outlast the settle timeout that is supposed to clear the slot",
            AssistService.FRAME_SLOT_WEDGED_MS > ProtocolClient.FRAME_SETTLE_TIMEOUT_MS
        )
    }
}
