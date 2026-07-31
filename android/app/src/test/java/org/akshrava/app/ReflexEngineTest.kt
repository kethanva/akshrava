package org.akshrava.app

import android.content.Context
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test
import org.mockito.Mockito.mock

class ReflexEngineTest {

    @Test
    fun disabledReflexIsFailClosed() {
        val engine = DisabledReflexEngine()
        assertFalse(engine.isArmed())
        assertNull(engine.evaluate(EncodedFrame(ByteArray(0), 1, 1)))
        engine.release()
    }

    @Test
    fun reflexFactoryCreatesDisabledEngineWhenFileMissingOrSmall() {
        val mockContext = mock(Context::class.java)
        val engine = ReflexFactory.create(mockContext)
        assertNotNull(engine)
        assertFalse(engine.isArmed())
    }
}
