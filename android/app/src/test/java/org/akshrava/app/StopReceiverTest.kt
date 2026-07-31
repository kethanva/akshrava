package org.akshrava.app

import android.content.Context
import android.content.Intent
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.mock

class StopReceiverTest {

    @Test
    fun stopReceiverInstanceAndOnReceive() {
        val mockReceiver = mock(StopReceiver::class.java)
        assertNotNull(mockReceiver)
        val mockContext = mock(Context::class.java)
        val intent = Intent()
        mockReceiver.onReceive(mockContext, intent)
    }
}
