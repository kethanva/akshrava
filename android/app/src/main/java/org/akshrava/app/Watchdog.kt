package org.akshrava.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock

/**
 * Platform-honest liveness check (§3.4). Modern Android forbids background starts of a camera
 * foreground service, so this never restarts anything silently. It only wakes every ~10 minutes,
 * and if a session that is meant to be running has gone silent it hands off to a loud, spoken
 * prompt asking the user to press Start again. The real recovery for a missed prompt is the
 * operator calling the user's contact.
 */
object Watchdog {
    private const val INTERVAL_MS = 10 * 60_000L
    private const val REQUEST_CODE = 7001

    fun schedule(context: Context) {
        val alarmManager = context.getSystemService(AlarmManager::class.java) ?: return
        // Inexact + allow-while-idle survives Doze without an exact-alarm permission; a liveness
        // check does not need second precision.
        alarmManager.setAndAllowWhileIdle(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            SystemClock.elapsedRealtime() + INTERVAL_MS,
            pendingIntent(context)
        )
    }

    fun cancel(context: Context) {
        context.getSystemService(AlarmManager::class.java)?.cancel(pendingIntent(context))
    }

    private fun pendingIntent(context: Context): PendingIntent {
        val intent = Intent(context, WatchdogReceiver::class.java)
        return PendingIntent.getBroadcast(
            context, REQUEST_CODE, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }
}
