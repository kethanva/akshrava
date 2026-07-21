package org.akshrava.app

import android.content.Context
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/**
 * Session-scoped NDJSON debug probe.
 * - Always mirrors to logcat (AkshravaAgentDebug)
 * - Appends to app filesDir/agent-debug.ndjson (adb pullable)
 * - Best-effort POST to host ingest when adb reverse is up
 */
internal object AgentDebugLog {
    private const val TAG = "AkshravaAgentDebug"
    private const val ENDPOINT = "http://127.0.0.1:7556/ingest/911b5a43-341e-4ab5-8c04-eafc8d141d9c"
    private const val SESSION_ID = "b00f63"
    private const val FILE_NAME = "agent-debug.ndjson"
    private val seq = AtomicLong(0)
    private val appContext = AtomicReference<Context?>(null)
    private val executor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "akshrava-agent-debug").apply { isDaemon = true }
    }

    fun bind(context: Context) {
        appContext.compareAndSet(null, context.applicationContext)
    }

    fun log(hypothesisId: String, location: String, message: String, data: Map<String, Any?> = emptyMap()) {
        val payload = JSONObject()
            .put("sessionId", SESSION_ID)
            .put("runId", "post-fix")
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
                    File(dir, FILE_NAME).appendText(line + "\n")
                }
            }
            runCatching {
                val conn = (URL(ENDPOINT).openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 800
                    readTimeout = 800
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    setRequestProperty("X-Debug-Session-Id", SESSION_ID)
                }
                conn.outputStream.use { it.write(line.toByteArray(Charsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            }
        }
    }
}
