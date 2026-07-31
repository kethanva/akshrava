package org.akshrava.app

import androidx.lifecycle.Lifecycle
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.mock
import org.mockito.Mockito.`when`

class CameraLifecycleOwnerTest {

    @Test
    fun cameraLifecycleOwnerMockableAndHasLifecycle() {
        val mockOwner = mock(CameraLifecycleOwner::class.java)
        val mockLifecycle = mock(Lifecycle::class.java)
        `when`(mockOwner.lifecycle).thenReturn(mockLifecycle)

        assertNotNull(mockOwner.lifecycle)
        mockOwner.resume()
        mockOwner.destroy()
    }
}
