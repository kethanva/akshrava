package org.akshrava.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AndroidSupportMatrixTest {
    @Test
    fun releaseMatrixCoversTheEightMajorGenerationsAndEveryIntermediateApi() {
        assertEquals(28, AndroidSupportMatrix.OLDEST_SUPPORTED_API)
        assertEquals(36, AndroidSupportMatrix.NEWEST_SUPPORTED_API)
        assertEquals(9, AndroidSupportMatrix.supportedApis().count())
        assertTrue(AndroidSupportMatrix.supportedApis().zipWithNext().all { (older, newer) -> newer == older + 1 })
    }
}
