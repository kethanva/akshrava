package org.akshrava.app

import android.content.Context
import android.os.SystemClock

/**
 * Small same-application record of "is a session meant to be running, and when did the camera
 * service last make progress". The watchdog reads it from an alarm broadcast to tell a genuine
 * OEM kill (active, but heartbeat gone stale) from an ordinary stop. Elapsed realtime is shared
 * across app processes and cannot jump when carrier/network time changes.
 */
object SessionFlags {
    private const val PREFS = "akshrava"
    private const val ACTIVE = "session_active"
    private const val HEARTBEAT = "heartbeat_ms"

    // A session whose heartbeat is older than this while still marked active was killed.
    const val STALE_AFTER_MS = 3 * 60_000L

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun setActive(context: Context, active: Boolean) {
        prefs(context).edit()
            .putBoolean(ACTIVE, active)
            .putLong(HEARTBEAT, SystemClock.elapsedRealtime())
            .apply()
    }

    fun heartbeat(context: Context) {
        prefs(context).edit().putLong(HEARTBEAT, SystemClock.elapsedRealtime()).apply()
    }

    fun isActive(context: Context): Boolean = prefs(context).getBoolean(ACTIVE, false)

    fun isStale(context: Context): Boolean =
        SystemClock.elapsedRealtime() - prefs(context).getLong(HEARTBEAT, 0L) > STALE_AFTER_MS
}
