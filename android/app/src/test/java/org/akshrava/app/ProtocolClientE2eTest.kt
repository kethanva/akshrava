package org.akshrava.app

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.mockito.Mockito
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Host JVM smoke against live GCP using the same ProtocolClient class.
 * Prefer the instrumented [GcpLiveProtocolClientE2eTest] via scripts/e2e_android_gcp.sh
 * for the durable Android OkHttp path on emulator/device.
 *
 * Requires: AKSHRAVA_WSS_URL and AKSHRAVA_TEST_TOKEN.
 */
class ProtocolClientE2eTest {
    companion object {
        private val FIXTURE_JPEG: ByteArray = Base64.getDecoder().decode(
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
                "KKKKACiiigAooooAKKKKAP/Z"
        )
    }

    @Test
    fun testLiveGcpProtocolClientEndToEnd() {
        val wssUrl = System.getenv("AKSHRAVA_WSS_URL") ?: ""
        val token = System.getenv("AKSHRAVA_TEST_TOKEN") ?: ""
        assumeTrue(
            "Skipping host JVM live E2E (set AKSHRAVA_WSS_URL + AKSHRAVA_TEST_TOKEN; prefer scripts/e2e_android_gcp.sh)",
            wssUrl.isNotBlank() && token.isNotBlank()
        )

        println("Running host JVM ProtocolClient smoke against: $wssUrl")
        val alertManager = Mockito.mock(AlertManager::class.java)
        val qualityLatch = CountDownLatch(1)
        val settledLatch = CountDownLatch(1)
        val lastQuality = AtomicReference<Quality?>(null)

        val client = ProtocolClient(
            endpoint = wssUrl,
            token = token,
            alertManager = alertManager,
            onState = { println("State update: $it") },
            onFrameSettled = { settledLatch.countDown() },
            onQuality = {
                lastQuality.set(it)
                qualityLatch.countDown()
            },
            language = "en-IN"
        )

        try {
            client.connect()
            var retries = 300
            while (retries > 0 && !client.canStream()) {
                Thread.sleep(100)
                retries--
            }
            assertTrue("Should connect with vision_enabled", client.canStream())

            val sent = client.sendFrame(
                frameId = 1,
                captureMonoMs = 100,
                pose = PoseSnapshot(-1000, 0, 10L),
                calibrationId = "e2e-r0",
                frame = EncodedFrame(FIXTURE_JPEG, 64, 64),
                mode = "normal",
                priority = false
            )
            assertTrue("Frame should be sent successfully", sent)
            assertTrue("Frame should settle", settledLatch.await(60, TimeUnit.SECONDS))
            assertTrue("Quality should arrive", qualityLatch.await(30, TimeUnit.SECONDS))
            assertTrue(lastQuality.get() != null)
        } finally {
            client.close()
        }
    }
}
