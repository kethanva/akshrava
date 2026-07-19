package org.akshrava.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

class ReflexEngineTest {
    @Test
    fun disabledReflexIsFailClosed() {
        val engine = DisabledReflexEngine()
        assertFalse(engine.isArmed())
        assertNull(engine.evaluate(EncodedFrame(ByteArray(0), 1, 1)))
    }
}
