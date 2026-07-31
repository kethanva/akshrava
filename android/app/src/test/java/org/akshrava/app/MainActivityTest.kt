package org.akshrava.app

import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.mock

class MainActivityTest {

    @Test
    fun mainActivityMockableAndInstantiable() {
        val mockActivity = mock(MainActivity::class.java)
        assertNotNull(mockActivity)
    }
}
