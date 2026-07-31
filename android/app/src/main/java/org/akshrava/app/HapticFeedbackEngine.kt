package org.akshrava.app

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

class HapticFeedbackEngine(context: Context) {
    private val vibrator: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val manager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
        manager?.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    }

    fun playBearingCue(bearing: String) {
        if (vibrator == null || !vibrator.hasVibrator()) return

        // Different patterns for left, ahead, right
        // Timings: wait, vibrate, wait, vibrate...
        val timings = when (bearing) {
            "left" -> longArrayOf(0, 100, 100, 100)       // Double pulse
            "right" -> longArrayOf(0, 300)                // One long pulse
            "ahead", "center" -> longArrayOf(0, 50, 50, 50, 50, 50) // Triple short pulse
            else -> return
        }

        val effect = VibrationEffect.createWaveform(timings, -1)
        vibrator.vibrate(effect)
    }

    fun playPattern(pattern: String) {
        if (vibrator == null || !vibrator.hasVibrator()) return

        val timings = when (pattern) {
            "single" -> longArrayOf(0, 80)
            "double" -> longArrayOf(0, 60, 90, 60)
            "triple" -> longArrayOf(0, 60, 70, 60, 70, 60)
            else -> return
        }
        
        vibrator.vibrate(VibrationEffect.createWaveform(timings, -1))
    }
}
