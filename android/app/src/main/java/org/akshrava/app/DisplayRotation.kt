package org.akshrava.app

import android.content.Context
import android.hardware.display.DisplayManager
import android.os.Build
import android.view.Display
import android.view.WindowManager

/**
 * Return the rotation of the default display.
 *
 * Services are not display-associated Contexts — never call [Context.getDisplay] here
 * (it throws UnsupportedOperationException and aborts CameraX bind).
 */
fun Context.currentDisplayRotation(): Int {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
        val fromManager = getSystemService(DisplayManager::class.java)
            ?.getDisplay(Display.DEFAULT_DISPLAY)
            ?.rotation
        if (fromManager != null) return fromManager
    }
    @Suppress("DEPRECATION")
    return (getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.rotation
}
