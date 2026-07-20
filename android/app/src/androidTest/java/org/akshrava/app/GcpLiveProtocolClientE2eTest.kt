package org.akshrava.app

import android.util.Base64
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Live Android OkHttp / ProtocolClient E2E against Cloud Run + remote vision.
 *
 * Requires instrumentation args (or matching env via adb shell):
 *   - akshrava_wss_url (default: BuildConfig.DEFAULT_WSS_ENDPOINT)
 *   - akshrava_test_token (Bearer JWT)
 */
@RunWith(AndroidJUnit4::class)
class GcpLiveProtocolClientE2eTest {
    companion object {
        // Same valid 64x64 JPEG fixture as scripts/e2e_android_protocol_gcp.sh
        private val FIXTURE_JPEG: ByteArray = Base64.decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/" +
                "2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCABAAEADASIAAhEBAxEB/" +
                "8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAk" +
                "M2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2" +
                "t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQD" +
                "BAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm" +
                "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/" +
                "9oADAMBAAIRAxEAPwDmqKKK/VT80CiiigAooooAKKKKACiiigBRRigUtfl3EfEeZYPMqlChUtFWsrRe8U+qvufunBvBuS5lktHFYqjzTlzXfNJbSklop" +
                "JbJdBMUGlpDRw5xHmWMzKnQr1Lxd7q0VtFvor7hxlwbkuW5LWxWFo8s48tnzSe8op6OTWzfQSiiiv1E/CwooooAUUtIKM1+XcR8OZljMyqV6FO8XazvFb" +
                "RS6u+5+6cG8ZZLluS0cLiq3LOPNdcsnvKTWqi1s11FpDRmg0cOcOZlg8yp169O0Ve7vF7xa6O+4cZcZZLmWS1sLha3NOXLZcsltKLerilsn1Eooor9RPws" +
                "KKKKACiiigAooooAKKKKAP/Z",
            Base64.DEFAULT
        )
    }

    @Test
    fun protocolClientLiveGcpReadyFrameQualityAndPing() {
        val args = InstrumentationRegistry.getArguments()
        val token = args.getString("akshrava_test_token")
            ?: System.getenv("AKSHRAVA_TEST_TOKEN")
            ?: ""
        assumeTrue(
            "Set instrumentation arg akshrava_test_token (minted RS256 device JWT)",
            token.isNotBlank()
        )
        val wssUrl = args.getString("akshrava_wss_url")
            ?: System.getenv("AKSHRAVA_WSS_URL")
            ?: BuildConfig.DEFAULT_WSS_ENDPOINT
        val calibrationId = args.getString("akshrava_calibration_id")
            ?: System.getenv("AKSHRAVA_CALIBRATION_ID")
            ?: "e2e-r0"

        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val alertManager = AlertManager(context, "en-IN")

        val qualityLatch = CountDownLatch(1)
        val settledLatch = CountDownLatch(1)
        val lastState = AtomicReference("")
        val lastQuality = AtomicReference<Quality?>(null)

        val client = ProtocolClient(
            endpoint = wssUrl,
            token = token,
            alertManager = alertManager,
            onState = { lastState.set(it) },
            onFrameSettled = { settledLatch.countDown() },
            onQuality = {
                lastQuality.set(it)
                qualityLatch.countDown()
            },
            language = "en-IN"
        )

        try {
            client.connect()
            var retries = 300 // up to 30s for ready + vision_enabled
            while (retries > 0 && !client.canStream()) {
                Thread.sleep(100)
                retries--
            }
            assertTrue(
                "ProtocolClient should reach canStream (ready + vision_enabled). state=${lastState.get()}",
                client.canStream()
            )
            assertEquals("Vision assistance connected", lastState.get())

            val captureMonoMs = android.os.SystemClock.elapsedRealtime()
            val frame = EncodedFrame(FIXTURE_JPEG, 64, 64)
            val pose = PoseSnapshot(-1000, 0, 10L)
            val sent = client.sendFrame(
                frameId = 1L,
                captureMonoMs = captureMonoMs,
                pose = pose,
                calibrationId = calibrationId,
                frame = frame,
                mode = "normal",
                priority = false
            )
            assertTrue("Frame should be accepted onto the wire", sent)

            assertTrue(
                "Frame should settle after remote result",
                settledLatch.await(60, TimeUnit.SECONDS)
            )
            assertTrue(
                "Server should send quality after a successful frame",
                qualityLatch.await(30, TimeUnit.SECONDS)
            )
            assertNotNull(lastQuality.get())
            assertTrue(lastQuality.get()!!.maxSide in 320..640)
        } finally {
            client.close()
            alertManager.shutdown()
        }

        // Application-level ping/pong on the same Android OkHttp stack + JWT.
        assertPingPong(wssUrl, token)
        println("ANDROID_GCP_E2E_PASS wss=$wssUrl vision_enabled=true")
    }

    private fun assertPingPong(wssUrl: String, token: String) {
        val readyLatch = CountDownLatch(1)
        val pongLatch = CountDownLatch(1)
        val readyVision = AtomicReference(false)
        val error = AtomicReference<String?>(null)
        val http = OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .build()
        val request = Request.Builder()
            .url(wssUrl)
            .header("Authorization", "Bearer $token")
            .build()
        val ws = http.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                val payload = runCatching { JSONObject(text) }.getOrNull() ?: return
                when (payload.optString("type")) {
                    "ready" -> {
                        readyVision.set(payload.optBoolean("vision_enabled", false))
                        readyLatch.countDown()
                    }
                    "pong" -> pongLatch.countDown()
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                error.set(t.message ?: "websocket failure")
                readyLatch.countDown()
                pongLatch.countDown()
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) = Unit
        })
        try {
            assertTrue("ping session ready: err=${error.get()}", readyLatch.await(45, TimeUnit.SECONDS))
            assertTrue("ping session vision_enabled", readyVision.get())
            assertTrue(ws.send(JSONObject().put("type", "ping").toString()))
            assertTrue("expected pong: err=${error.get()}", pongLatch.await(15, TimeUnit.SECONDS))
            assertEquals(null, error.get())
        } finally {
            ws.close(1000, "e2e done")
            http.dispatcher.executorService.shutdown()
        }
    }
}
