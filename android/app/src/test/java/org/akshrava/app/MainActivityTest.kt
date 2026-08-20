package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito.mock

class MainActivityTest {

    @Test
    fun mainActivityMockableAndInstantiable() {
        val mockActivity = mock(MainActivity::class.java)
        assertNotNull(mockActivity)
    }

    @Test
    fun startingCopyDoesNotTellTheUserToLockTheScreen() {
        val en = resourceFile("values/strings.xml").readText()
        val hi = resourceFile("values-hi/strings.xml").readText()
        assertTrue(en.contains("do not lock"))
        assertFalse(en.contains("You can now lock"))
        assertTrue(hi.contains("लॉक न करें"))
    }

    private fun resourceFile(relative: String): java.io.File {
        val candidates = listOf(
            java.io.File("src/main/res/$relative"),
            java.io.File("app/src/main/res/$relative"),
            java.io.File("android/app/src/main/res/$relative")
        )
        return candidates.first { it.isFile }
    }
}
