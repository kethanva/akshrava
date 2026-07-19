package org.akshrava.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {
    private lateinit var endpoint: EditText
    private lateinit var token: EditText
    private lateinit var calibration: EditText
    private lateinit var status: TextView

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        if (result[Manifest.permission.CAMERA] == true) startServiceIfConfigured()
        else status.text = "Camera permission is required to start assistance."
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val config = AppConfigStore.load(this)
        endpoint = field("Secure WebSocket endpoint", config.endpoint)
        token = field("Device token", config.deviceToken)
        calibration = field("Calibration ID", config.calibrationId)
        status = TextView(this).apply { text = "Configure once with a volunteer, then press Start assistance."; textSize = 18f }
        val start = Button(this).apply {
            text = getString(R.string.action_start)
            contentDescription = getString(R.string.action_start)
            setOnClickListener { requestAndStart() }
        }
        val stop = Button(this).apply {
            text = getString(R.string.action_stop)
            contentDescription = getString(R.string.action_stop)
            setOnClickListener { stopService(Intent(this@MainActivity, AssistService::class.java)); status.text = "Assistance stopped." }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 48, 32, 32)
            addView(status)
            addView(endpoint); addView(token); addView(calibration); addView(start); addView(stop)
        })
    }

    private fun field(hint: String, value: String) = EditText(this).apply {
        this.hint = hint
        setText(value)
        textSize = 18f
        contentDescription = hint
    }

    private fun requestAndStart() {
        saveConfig()
        val required = mutableListOf(Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) required.add(Manifest.permission.POST_NOTIFICATIONS)
        val missing = required.filter { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isEmpty()) startServiceIfConfigured() else permissions.launch(missing.toTypedArray())
    }

    private fun saveConfig() {
        val previous = AppConfigStore.load(this)
        AppConfigStore.save(this, previous.copy(endpoint = endpoint.text.toString(), deviceToken = token.text.toString(), calibrationId = calibration.text.toString()))
    }

    private fun startServiceIfConfigured() {
        val config = AppConfigStore.load(this)
        val endpointUri = Uri.parse(config.endpoint)
        // Debug builds may use cleartext only for an emulator/local mock. A debug APK must not
        // silently carry a bearer token over an arbitrary café or staging Wi-Fi network.
        val localMock = BuildConfig.DEBUG && endpointUri.scheme == "ws" &&
            endpointUri.host in setOf("127.0.0.1", "localhost", "10.0.2.2")
        val secureEndpoint = endpointUri.scheme == "wss" || localMock
        if (!secureEndpoint || config.deviceToken.isBlank() || config.calibrationId.isBlank()) {
            status.text = "A volunteer must enter a WSS endpoint, device token, and calibration ID."
            return
        }
        requestBatteryExemption()
        val intent = Intent(this, AssistService::class.java).setAction(AssistService.ACTION_START)
        ContextCompat.startForegroundService(this, intent)
        status.text = "Starting assistance. You can now lock the screen."
    }

    // Accessibility-class exemption so OEM battery savers stop killing the foreground camera
    // service (§3.4). The volunteer approves it once at provisioning.
    private fun requestBatteryExemption() {
        val power = getSystemService(PowerManager::class.java) ?: return
        if (!power.isIgnoringBatteryOptimizations(packageName)) {
            runCatching {
                startActivity(
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
                )
            }
        }
    }
}
