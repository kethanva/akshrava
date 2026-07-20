package org.akshrava.app

import java.net.URI

/**
 * Stops a physical handset from mistaking its own loopback address for the live vision API.
 *
 * Plain ws:// loopback remains available for deliberate debug development.  A physical phone
 * needs an explicit build-time opt-in because 127.0.0.1 otherwise points at the handset, not a
 * developer workstation or Cloud Run.
 */
object EndpointPolicy {
    enum class EndpointClass(val logValue: String) {
        LIVE_SECURE("live_secure"),
        LOCAL_DEVELOPMENT("local_development"),
        INVALID("invalid")
    }

    data class Decision(
        val allowed: Boolean,
        val endpointClass: EndpointClass,
        val message: String? = null
    )

    private val loopbackHosts = setOf("127.0.0.1", "localhost", "10.0.2.2", "::1")

    fun evaluate(
        endpoint: String,
        debugBuild: Boolean,
        isEmulator: Boolean,
        allowPhysicalLoopbackDevelopment: Boolean = false
    ): Decision {
        val parsed = runCatching { URI(endpoint.trim()) }.getOrNull()
            ?: return Decision(false, EndpointClass.INVALID, "Enter a valid secure WSS endpoint.")
        val scheme = parsed.scheme?.lowercase()
        val host = parsed.host?.lowercase()
        if (scheme == "wss" && !host.isNullOrBlank()) {
            return Decision(true, EndpointClass.LIVE_SECURE)
        }
        if (scheme == "ws" && host in loopbackHosts) {
            val permitted = debugBuild && (isEmulator || allowPhysicalLoopbackDevelopment)
            return if (permitted) {
                Decision(true, EndpointClass.LOCAL_DEVELOPMENT)
            } else {
                Decision(
                    false,
                    EndpointClass.LOCAL_DEVELOPMENT,
                    "This phone is configured for local development. Provision the live secure WSS endpoint."
                )
            }
        }
        return Decision(false, EndpointClass.INVALID, "Use a secure WSS endpoint for assistance.")
    }

    fun classify(endpoint: String): EndpointClass =
        evaluate(endpoint, debugBuild = false, isEmulator = false).endpointClass
}
