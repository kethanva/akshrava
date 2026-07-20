package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EndpointPolicyTest {
    @Test
    fun liveSecureEndpointIsAllowedOnPhysicalPhone() {
        val decision = EndpointPolicy.evaluate(
            "wss://akshrava-api.example/v1/session", debugBuild = false, isEmulator = false
        )
        assertTrue(decision.allowed)
        assertTrue(decision.endpointClass == EndpointPolicy.EndpointClass.LIVE_SECURE)
    }

    @Test
    fun loopbackOnPhysicalHandsetIsRejectedUnlessExplicitlyOptedIn() {
        val defaultDecision = EndpointPolicy.evaluate(
            "ws://127.0.0.1:8000/v1/session", debugBuild = true, isEmulator = false
        )
        assertFalse(defaultDecision.allowed)
        assertTrue(defaultDecision.message!!.contains("local development"))
        assertTrue(
            EndpointPolicy.evaluate(
                "ws://127.0.0.1:8000/v1/session",
                debugBuild = true,
                isEmulator = false,
                allowPhysicalLoopbackDevelopment = true
            ).allowed
        )
    }

    @Test
    fun loopbackRemainsAvailableToDebugEmulator() {
        assertTrue(
            EndpointPolicy.evaluate(
                "ws://10.0.2.2:8000/v1/session", debugBuild = true, isEmulator = true
            ).allowed
        )
    }
}
