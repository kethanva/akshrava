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
}
