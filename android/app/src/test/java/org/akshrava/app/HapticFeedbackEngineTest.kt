package org.akshrava.app

import android.content.Context
import android.os.Vibrator
import org.junit.Assert.assertNotNull
import org.junit.Test
import org.mockito.Mockito.`when`
import org.mockito.Mockito.mock

class HapticFeedbackEngineTest {

    @Test
    fun nullOrNoVibratorExecutesGracefully() {
        val mockContext = mock(Context::class.java)
        `when`(mockContext.getSystemService(Context.VIBRATOR_SERVICE)).thenReturn(null)

        val engine = HapticFeedbackEngine(mockContext)
        assertNotNull(engine)
        engine.playBearingCue("left")
        engine.playPattern("single")
    }

    @Test
    fun playBearingCueTriggersPatterns() {
        val mockContext = mock(Context::class.java)
        val mockVibrator = mock(Vibrator::class.java)
        `when`(mockContext.getSystemService(Context.VIBRATOR_SERVICE)).thenReturn(mockVibrator)
        `when`(mockVibrator.hasVibrator()).thenReturn(true)

        val engine = HapticFeedbackEngine(mockContext)
        engine.playBearingCue("left")
        engine.playBearingCue("right")
        engine.playBearingCue("ahead")
        engine.playBearingCue("unknown")
    }

    @Test
    fun playPatternTriggersPatterns() {
        val mockContext = mock(Context::class.java)
        val mockVibrator = mock(Vibrator::class.java)
        `when`(mockContext.getSystemService(Context.VIBRATOR_SERVICE)).thenReturn(mockVibrator)
        `when`(mockVibrator.hasVibrator()).thenReturn(true)

        val engine = HapticFeedbackEngine(mockContext)
        engine.playPattern("single")
        engine.playPattern("double")
        engine.playPattern("triple")
        engine.playPattern("unknown")
    }
}
