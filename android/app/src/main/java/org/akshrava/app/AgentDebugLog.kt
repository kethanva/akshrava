package org.akshrava.app

import android.content.Context
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * Session-scoped NDJSON debug probe.
 * - Always mirrors to logcat (AkshravaAgentDebug)
 * - Appends to app filesDir/agent-debug.ndjson (adb pullable)
 *
 * It never posts debug records off-device. A hard-coded workstation collector made field
 * diagnostics depend on an adb-specific endpoint and added unnecessary network work during a
 * failure. The file is bounded so an extended walk cannot consume device storage.
 */
internal object AgentDebugLog {
    private const val TAG = "AkshravaAgentDebug"
    private const val FILE_NAME = "agent-debug.ndjson"
    private const val MAX_FILE_BYTES = 512 * 1024L
    private val seq = AtomicLong(0)
    private val appContext = AtomicReference<Context?>(null)
    @Volatile private var enabled = false
    private val executor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "akshrava-agent-debug").apply { isDaemon = true }
    }

    fun bind(context: Context, debugEnabled: Boolean) {
        appContext.set(context.applicationContext)
        enabled = debugEnabled
    }

    fun log(hypothesisId: String, location: String, message: String, data: Map<String, Any?> = emptyMap()) {
        if (!enabled) return
        val payload = JSONObject()
            .put("hypothesisId", hypothesisId)
            .put("location", location)
            .put("message", message)
            .put("timestamp", System.currentTimeMillis())
            .put("monoMs", SystemClock.elapsedRealtime())
            .put("id", "log_${System.currentTimeMillis()}_${seq.incrementAndGet()}")
        val dataObj = JSONObject()
        for ((k, v) in data) dataObj.put(k, v ?: JSONObject.NULL)
        payload.put("data", dataObj)
        val line = payload.toString()
        Log.i(TAG, line)
        executor.execute {
            runCatching {
                appContext.get()?.filesDir?.let { dir ->
                    val file = File(dir, FILE_NAME)
                    if (file.length() >= MAX_FILE_BYTES) file.delete()
                    file.appendText(line + "\n")
                }
            }
        }
    }
}
