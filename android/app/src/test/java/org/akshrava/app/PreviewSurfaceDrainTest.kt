package org.akshrava.app

import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.mock

class PreviewSurfaceDrainTest {

    @Test
    fun previewSurfaceDrainInstanceAndRelease() {
        val drain = PreviewSurfaceDrain()
        assertNotNull(drain)
        drain.release()
    }

    @Test
    fun previewSurfaceDrainMockable() {
        val mockDrain = mock(PreviewSurfaceDrain::class.java)
        assertNotNull(mockDrain)
        mockDrain.release()
    }
}
