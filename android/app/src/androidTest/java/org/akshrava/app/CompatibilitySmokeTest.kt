package org.akshrava.app

import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Runs on every API in the release matrix. Launching must remain safe before a device has been
 * provisioned or granted camera permission; the user must explicitly start assistance afterwards.
 */
@RunWith(AndroidJUnit4::class)
class CompatibilitySmokeTest {
    @Test
    fun unprovisionedActivityLaunchesWithoutStartingCameraService() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            scenario.onActivity { activity ->
                check(!SessionFlags.isActive(activity))
            }
        }
    }
}
