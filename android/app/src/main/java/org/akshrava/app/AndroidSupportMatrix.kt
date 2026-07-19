package org.akshrava.app

/**
 * The release validation matrix is Android 9–16 (API 28–36), including Android 12L/API 32.
 * That covers every platform API across the latest eight major Android generations, rather than
 * silently skipping the 12L compatibility release.
 * API 26–27 remain build-compatible legacy devices, but field qualification is intentionally
 * limited to the documented Tier-A Android 10+ fleet.
 */
object AndroidSupportMatrix {
    const val OLDEST_SUPPORTED_API = 28
    const val NEWEST_SUPPORTED_API = 36

    fun supportedApis(): IntRange = OLDEST_SUPPORTED_API..NEWEST_SUPPORTED_API
}
