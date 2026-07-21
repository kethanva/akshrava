package org.akshrava.app

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.FrameLayout

/**
 * Keeps the display awake so OEM ROMs that stop CameraX when the screen sleeps continue
 * delivering real frames.
 *
 * Preferred path: a 1×1 overlay with FLAG_KEEP_SCREEN_ON (needs SYSTEM_ALERT_WINDOW).
 * Fallback: SCREEN_BRIGHT_WAKE_LOCK when overlay permission is missing — without this, the
 * display sleeps, ImageAnalysis goes near-black, dark TTS still fires, and detection uploads
 * stop while the WebSocket looks healthy.
 */
class ScreenKeepAlive(private val context: Context) {
    private var overlay: View? = null
    private var screenWakeLock: PowerManager.WakeLock? = null
    private val wm = context.getSystemService(WindowManager::class.java)

    /** How the display is being held, for diagnostics. */
    enum class Mode { NONE, OVERLAY, WAKE_LOCK }

    @Volatile var mode: Mode = Mode.NONE
        private set

    /**
     * Returns true when the display is actually being held awake (overlay or wake-lock fallback).
     */
    fun start(): Boolean {
        if (isHoldingScreenOn()) return true
        if (startOverlay()) {
            mode = Mode.OVERLAY
            return true
        }
        if (startWakeLockFallback()) {
            mode = Mode.WAKE_LOCK
            return true
        }
        mode = Mode.NONE
        return false
    }

    private fun startOverlay(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(context)) {
            return false
        }
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }
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
        return try {
            wm.addView(view, params)
            overlay = view
            true
        } catch (_: Exception) {
            overlay = null
            false
        }
    }

    /**
     * Last-resort keep-awake when the volunteer has not granted overlay permission.
     * Deprecated on modern Android, but still honored by many OEM ROMs for a foreground
     * camera service — and far better than silently uploading nothing after the panel sleeps.
     */
    private fun startWakeLockFallback(): Boolean {
        val pm = context.getSystemService(PowerManager::class.java) ?: return false
        return try {
            @Suppress("DEPRECATION")
            val wl = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ON_AFTER_RELEASE,
                "Akshrava:screenKeepAlive"
            )
            wl.setReferenceCounted(false)
            // Match AssistService partial-wake budget so a hung teardown cannot hold the panel forever.
            wl.acquire(60 * 60_000L)
            screenWakeLock = wl
            true
        } catch (_: Exception) {
            screenWakeLock = null
            false
        }
    }

    fun isHoldingScreenOn(): Boolean =
        overlay != null || (screenWakeLock?.isHeld == true)

    fun stop() {
        val view = overlay
        overlay = null
        if (view != null) {
            try {
                wm.removeView(view)
            } catch (_: Exception) {
            }
        }
        val wl = screenWakeLock
        screenWakeLock = null
        if (wl != null && wl.isHeld) {
            runCatching { wl.release() }
        }
        mode = Mode.NONE
    }
}
