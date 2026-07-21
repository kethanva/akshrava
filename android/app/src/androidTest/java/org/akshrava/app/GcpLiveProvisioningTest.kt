package org.akshrava.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Debug-instrumentation-only provisioning path for a physically attached pilot phone.
 * The bearer remains an instrumentation argument and is immediately Keystore-encrypted by the
 * target app; this test deliberately never prints or compares its value.
 */
@RunWith(AndroidJUnit4::class)
class GcpLiveProvisioningTest {
    @Test
    fun provisionTargetAppWithLiveEndpointAndShortLivedToken() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue(args.getString("akshrava_provision_target") == "true")
        val endpoint = args.getString("akshrava_wss_url") ?: ""
        val token = args.getString("akshrava_test_token") ?: ""
        val calibrationId = args.getString("akshrava_calibration_id") ?: ""
        assumeTrue("Live endpoint, token, and calibration are required", endpoint.isNotBlank() && token.isNotBlank() && calibrationId.isNotBlank())
        val decision = EndpointPolicy.evaluate(endpoint, debugBuild = true, isEmulator = false)
        assertTrue("Target provisioning must use a live secure WSS endpoint", decision.allowed)
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assertTrue(AppConfigStore.save(context, AppConfig(endpoint, token, "en-IN", calibrationId, false)))
        val saved = AppConfigStore.load(context)
        assertEquals(endpoint, saved.endpoint)
        assertEquals(calibrationId, saved.calibrationId)
        assertTrue("Token must be readable from the target Keystore", saved.deviceToken.isNotBlank())
    }
}
