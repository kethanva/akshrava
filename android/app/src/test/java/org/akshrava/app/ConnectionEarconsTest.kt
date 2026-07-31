package org.akshrava.app

import android.media.ToneGenerator
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.mock

class ConnectionEarconsTest {
    @Test
    fun durationsStayShortForNoisyStreetUse() {
        assertEquals(100, ConnectionEarcons.CONNECTED_MS)
        assertEquals(150, ConnectionEarcons.DROPPED_MS)
        assertEquals(120, ConnectionEarcons.RESTORED_MS)
        assertEquals(50, ConnectionEarcons.STALE_TICK_MS)
        assert(ConnectionEarcons.DROPPED_MS < 500)
    }

    @Test
    fun instanceMethodsWithNullToneGeneratorExecuteGracefully() {
        val earcons = ConnectionEarcons(tones = null)
        assertNotNull(earcons)
        earcons.connected()
        earcons.dropped()
        earcons.restored()
        earcons.staleTick()
        earcons.lookFailed()
        earcons.reconnectPending()
        earcons.release()
    }

    @Test
    fun instanceMethodsWithMockToneGeneratorExecuteGracefully() {
        val mockTone = mock(ToneGenerator::class.java)
        val earcons = ConnectionEarcons(tones = mockTone)
        earcons.connected()
        earcons.dropped()
        earcons.restored()
        earcons.staleTick()
        earcons.lookFailed()
        earcons.reconnectPending()
        earcons.release()
    }
}
