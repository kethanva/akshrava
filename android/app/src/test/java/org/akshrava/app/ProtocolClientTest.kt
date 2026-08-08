package org.akshrava.app

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock

class ProtocolClientTest {

    // ---- protocol capability negotiation (A-7) ----
    //
    // The phone cannot be redeployed with the server, so compatibility has to be negotiated per
    // connection rather than inferred from a build number. Everything here fails CLOSED: an
    // unknown server gets the conservative legacy behaviour.

    @Test
    fun `capabilities are parsed from the ready payload`() {
        val payload = JSONObject(
            """{"type":"ready","protocol_version":1,"capabilities":["pose_cdeg_full_range","result_acknowledgement","other"]}"""
        )
        val parsed = ProtocolClient.parseCapabilities(payload)
        assertTrue(parsed.contains(ProtocolClient.CAPABILITY_POSE_CDEG_FULL_RANGE))
        assertTrue(parsed.contains(ProtocolClient.CAPABILITY_RESULT_ACKNOWLEDGEMENT))
        assertTrue(parsed.contains("other"))
    }

    @Test
    fun `a ready payload without capabilities negotiates nothing`() {
        // This is the old-server case and must stay the safe default forever.
        val payload = JSONObject("""{"type":"ready","vision_enabled":true}""")
        assertTrue(ProtocolClient.parseCapabilities(payload).isEmpty())
    }

    @Test
    fun `a malformed capabilities field never fails the connection`() {
        // A capability list is an optimisation hint. Refusing to connect over one would take
        // assistance away from a user for a cosmetic reason.
        for (raw in listOf(
            """{"capabilities":"not-an-array"}""",
            """{"capabilities":[]}""",
            """{"capabilities":[""," "]}""",
            """{"capabilities":null}"""
        )) {
            assertTrue(
                "malformed capabilities $raw must degrade to empty, not throw",
                ProtocolClient.parseCapabilities(JSONObject(raw)).isEmpty()
            )
        }
    }

    @Test
    fun `pose is clamped to the legacy floor when the server has not advertised full range`() {
        // Below the old floor the legacy server closes the socket, which the user hears as
        // assistance dying and coming back. Omit the field instead.
        assertNull(ProtocolClient.wirePoseCdeg(-12_000, serverAcceptsFullPoseRange = false))
        assertEquals(-9_000, ProtocolClient.wirePoseCdeg(-9_000, serverAcceptsFullPoseRange = false))
        assertEquals(4_500, ProtocolClient.wirePoseCdeg(4_500, serverAcceptsFullPoseRange = false))
    }

    @Test
    fun `pose uses the full documented range once the server advertises support`() {
        assertEquals(-12_000, ProtocolClient.wirePoseCdeg(-12_000, serverAcceptsFullPoseRange = true))
        // Still clamped to the documented wire bounds, capability or not.
        assertEquals(
            ProtocolClient.POSE_CDEG_MIN,
            ProtocolClient.wirePoseCdeg(-99_000, serverAcceptsFullPoseRange = true)
        )
        assertEquals(
            ProtocolClient.POSE_CDEG_MAX,
            ProtocolClient.wirePoseCdeg(99_000, serverAcceptsFullPoseRange = true)
        )
    }

    @Test
    fun `wirePoseCdeg defaults to legacy behaviour when the caller does not negotiate`() {
        // Fail closed: an un-negotiated caller must not be the one that reintroduces the flap.
        assertNull(ProtocolClient.wirePoseCdeg(-12_000))
    }
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
        assertEquals(null, ProtocolClient.wirePoseCdeg(-12_500))
        assertEquals(null, ProtocolClient.wirePoseCdeg(-9_001))
        assertEquals(-9_000, ProtocolClient.wirePoseCdeg(-9_000))
        assertEquals(-1_000, ProtocolClient.wirePoseCdeg(-1_000))
        assertEquals(1_800, ProtocolClient.wirePoseCdeg(1_800))
        assertEquals(18_000, ProtocolClient.wirePoseCdeg(20_000))
    }

    @Test
    fun wedgedSlotThresholdSitsAboveTheTimeoutMeantToPreventIt() {
        assertTrue(
            "wedge threshold must outlast the settle timeout that is supposed to clear the slot",
            AssistService.FRAME_SLOT_WEDGED_MS > ProtocolClient.FRAME_SETTLE_TIMEOUT_MS
        )
    }

    @Test
    fun protocolClientMockable() {
        val mockClient = mock(ProtocolClient::class.java)
        assertNotNull(mockClient)
        mockClient.connect()
        mockClient.close()
    }
}
