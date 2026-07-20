package org.akshrava.app

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Build
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout

/**
 * Keeps the screen barely on so OEM ROMs that kill CameraX when the display sleeps
 * continue delivering frames. Brightness ≈1%. Requires overlay permission when available;
 * otherwise this is a no-op and the volunteer must leave the screen unlocked.
 */
class ScreenKeepAlive(private val context: Context) {
    private var overlay: View? = null
    private val wm = context.getSystemService(WindowManager::class.java)

    fun start() {
        if (overlay != null) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(context)) {
            return
        }
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }
        // Keep a tiny, nearly invisible overlay — a full-screen layer blocked the UI after Start.
        val params = WindowManager.LayoutParams(
            1,
            1,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.BOTTOM or Gravity.START
            screenBrightness = 0.01f
        }
        val view = FrameLayout(context).apply { setBackgroundColor(Color.TRANSPARENT) }
        try {
            wm.addView(view, params)
            overlay = view
        } catch (_: Exception) {
            overlay = null
        }
    }

    fun stop() {
        val view = overlay ?: return
        try {
            wm.removeView(view)
        } catch (_: Exception) {
        }
        overlay = null
    }
}
