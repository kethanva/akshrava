package org.akshrava.app

import android.app.ActivityManager
import android.content.Context
import android.os.Build

/**
 * Capture defaults for donated / older phones within [Build.VERSION_CODES.O] (minSdk 26).
 *
 * Pre-Oreo devices are out of install range (NotificationChannel + FGS camera type policy).
 * Within the supported floor, low-RAM and older-API devices start on a cheaper ladder so the
 * recent 3G/4G optimizations do not begin at 640/Q55 on a 2 GB phone.
 */
object DeviceCapability {
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
}
