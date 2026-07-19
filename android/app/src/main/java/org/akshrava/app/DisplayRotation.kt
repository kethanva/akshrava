package org.akshrava.app

import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Build
import android.view.Display
import android.view.Surface
import android.view.WindowManager

/** Return the rotation of this context's display without relying on the deprecated default display. */
fun Context.currentDisplayRotation(): Int {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
        return display?.rotation
            ?: getSystemService(DisplayManager::class.java)?.getDisplay(Display.DEFAULT_DISPLAY)?.rotation
            ?: Surface.ROTATION_0
    }
    @Suppress("DEPRECATION")
    return (getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.rotation
}
