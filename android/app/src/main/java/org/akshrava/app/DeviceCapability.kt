package org.akshrava.app

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.os.BatteryManager
import android.os.Build
import kotlin.math.roundToInt

/**
 * Capture defaults for donated / older phones within [Build.VERSION_CODES.O] (minSdk 26).
 *
 * Pre-Oreo devices are out of install range (NotificationChannel + FGS camera type policy).
 * Within the supported floor, low-RAM and older-API devices start on a cheaper ladder so the
 * recent 3G/4G optimizations do not begin at 640/Q55 on a 2 GB phone.
 */
object DeviceCapability {
    /** Avoid treating a physical handset's 127.0.0.1 as a developer workstation. */
    fun isEmulator(): Boolean =
        Build.FINGERPRINT.startsWith("generic") ||
            Build.FINGERPRINT.startsWith("unknown") ||
            Build.MODEL.contains("google_sdk", ignoreCase = true) ||
            Build.MODEL.contains("Emulator", ignoreCase = true) ||
            Build.MODEL.contains("Android SDK built for", ignoreCase = true) ||
            Build.MANUFACTURER.contains("Genymotion", ignoreCase = true) ||
            (Build.BRAND.startsWith("generic") && Build.DEVICE.startsWith("generic")) ||
            "google_sdk" == Build.PRODUCT

    /** True for ActivityManager low-RAM profiles or &lt; 3 GB total memory. */
    fun isConstrained(context: Context): Boolean {
        val am = context.getSystemService(ActivityManager::class.java) ?: return false
        if (am.isLowRamDevice) return true
        val info = ActivityManager.MemoryInfo()
        am.getMemoryInfo(info)
        // totalMem is API 16+; treat under ~2.8 GiB as constrained donated hardware to avoid
        // incorrectly penalizing true 3GB phones (which report ~2.7-2.8GB due to kernel/GPU reserved RAM).
        return info.totalMem in 1 until (2.8 * 1024 * 1024 * 1024).toLong()
    }

    /** Initial quality before the first server `quality` hint. */
    fun initialQuality(context: Context): Quality {
        if (!isConstrained(context)) return Quality()
        // Match LinkQualityController mid stress: small enough for 3G uplink + weak CPUs.
        return LinkQualityController.STRESS_STEPS[1]
    }

    /** Cap CameraX analysis side so NV21 scratch stays bounded on low-RAM devices. */
    fun analysisSideCap(context: Context): Int =
        if (isConstrained(context)) 480 else 640

    /** API 26–29 lack some camera2 quirks fixed later; keep a slightly longer settle bias. */
    fun preferConservativeSettle(context: Context): Boolean =
        isConstrained(context) || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q

    /**
     * Percent-per-hour a live assistance session costs on the hardware this targets.
     *
     * A single coarse figure, not a measurement: capture rate, screen-on time, radio, and battery
     * health all move it. Everything derived from it is phrased as an estimate for that reason.
     */
    internal const val SESSION_DRAIN_PERCENT_PER_HOUR = 4.0

    /** Battery percent from a sticky ACTION_BATTERY_CHANGED intent, or null if unreadable. */
    fun batteryPercent(batteryStatus: Intent?): Int? {
        val level = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        if (level < 0 || scale <= 0) return null
        return (level * 100f / scale).roundToInt().coerceIn(0, 100)
    }

    /**
     * Spoken F-15 battery gauge.
     *
     * The remaining-time figure is a planning aid for someone who cannot glance at a status bar,
     * so it is stated as an estimate and never rounded down to "zero hours" while the phone is
     * still running — a user deciding whether to start a walk must not be told the session is
     * already over when it is not.
     */
    fun batteryStatusText(pct: Int?): String {
        if (pct == null) return "Battery level unavailable."
        val hours = pct / SESSION_DRAIN_PERCENT_PER_HOUR
        val estimate = when {
            hours >= 1.5 -> "roughly ${hours.roundToInt()} hours"
            hours >= 0.75 -> "roughly one hour"
            else -> "less than an hour"
        }
        return "Battery at $pct percent. Estimated $estimate of assistance time remaining."
    }
}
