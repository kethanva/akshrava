package org.akshrava.app

import org.junit.Assert.assertTrue
import org.junit.Test
import org.mockito.Mockito
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class ProtocolClientE2eTest {
    @Test
    fun testLiveGcpProtocolClientEndToEnd() {
        val wssUrl = System.getenv("AKSHRAVA_WSS_URL") ?: ""
        val token = System.getenv("AKSHRAVA_TEST_TOKEN") ?: ""
        if (wssUrl.isBlank() || token.isBlank()) {
            println("Skipping live GCP E2E test (env AKSHRAVA_WSS_URL/AKSHRAVA_TEST_TOKEN not set)")
            return
        }

        println("Running live GCP E2E against: $wssUrl")
        val alertManager = Mockito.mock(AlertManager::class.java)
        val latch = CountDownLatch(1)
        var resultReceived = false

        val client = ProtocolClient(
            endpoint = wssUrl,
            token = token,
            alertManager = alertManager,
            onState = { state ->
                println("State update: $state")
            },
            onFrameSettled = {
                println("Frame settled!")
                resultReceived = true
                latch.countDown()
            },
            onQuality = { quality ->
                println("Quality update: $quality")
            }
        )

        client.connect()

        // Wait up to 10 seconds for connection setup and vision connected status
        var retries = 100
        while (retries > 0 && !client.canStream()) {
            Thread.sleep(100)
            retries--
        }

        assertTrue("Should connect and state machine ready to stream", client.canStream())

        // Decode the 64x64 valid JPEG fixture
        val jpegBase64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/" +
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

        val fakeJpeg = Base64.getDecoder().decode(jpegBase64)
        val fakeFrame = EncodedFrame(fakeJpeg, 64, 64)
        val pose = PoseSnapshot(-1000, 0, 10L)

        val sent = client.sendFrame(
            frameId = 1,
            captureMonoMs = 100,
            pose = pose,
            calibrationId = "e2e-r0",
            frame = fakeFrame
        )

        assertTrue("Frame should be sent successfully", sent)

        // Wait for frame analysis and settle trigger
        val completed = latch.await(15, TimeUnit.SECONDS)
        assertTrue("Latch should complete (frame settled response received)", completed)
        assertTrue("Result should be received", resultReceived)

        client.close()
    }
}
