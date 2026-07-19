package org.akshrava.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

class MainActivity : AppCompatActivity() {
    private lateinit var endpoint: EditText
    private lateinit var token: EditText
    private lateinit var calibration: EditText
    private lateinit var language: Spinner
    private lateinit var status: TextView

    private val languageTags = listOf("en-IN", "hi-IN")

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        when {
            !hasPermission(Manifest.permission.CAMERA) -> {
                status.text = getString(R.string.status_camera_permission)
            }
            needsNotificationPermission() && !hasPermission(Manifest.permission.POST_NOTIFICATIONS) -> {
                status.text = getString(R.string.status_notification_permission)
            }
            else -> startServiceIfConfigured()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val config = AppConfigStore.load(this)
        endpoint = field(getString(R.string.hint_endpoint), config.endpoint)
        token = field(getString(R.string.hint_token), config.deviceToken).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO
        }
        calibration = field(getString(R.string.hint_calibration), config.calibrationId)
        language = Spinner(this).apply {
            adapter = ArrayAdapter(
                this@MainActivity,
                android.R.layout.simple_spinner_dropdown_item,
                listOf(getString(R.string.language_english), getString(R.string.language_hindi))
            )
            contentDescription = getString(R.string.hint_language)
            setSelection(languageTags.indexOf(config.language).coerceAtLeast(0))
        }
        status = TextView(this).apply {
            text = getString(R.string.status_configure)
            textSize = 18f
        }
        val start = Button(this).apply {
            text = getString(R.string.action_start)
            contentDescription = getString(R.string.action_start)
            setOnClickListener { requestAndStart() }
        }
        val stop = Button(this).apply {
            text = getString(R.string.action_stop)
            contentDescription = getString(R.string.action_stop)
            setOnClickListener {
                stopService(Intent(this@MainActivity, AssistService::class.java))
                status.text = getString(R.string.status_stopped)
            }
        }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 48, 32, 32)
            addView(status)
            addView(endpoint)
            addView(token)
            addView(calibration)
            addView(language)
            addView(start)
            addView(stop)
        })
    }

    private fun field(hint: String, value: String) = EditText(this).apply {
        this.hint = hint
        setText(value)
        textSize = 18f
        contentDescription = hint
    }

    private fun requestAndStart() {
        if (!saveConfig()) return
        val required = mutableListOf(Manifest.permission.CAMERA)
        if (needsNotificationPermission()) required.add(Manifest.permission.POST_NOTIFICATIONS)
        val missing = required.filterNot(::hasPermission)
        if (missing.isEmpty()) startServiceIfConfigured() else permissions.launch(missing.toTypedArray())
    }

    private fun needsNotificationPermission(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU

    private fun hasPermission(permission: String): Boolean =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private fun saveConfig(): Boolean {
        val previous = AppConfigStore.load(this)
        val selectedLanguage = languageTags.getOrElse(language.selectedItemPosition) { "en-IN" }
        val saved = AppConfigStore.save(
            this,
            previous.copy(
                endpoint = endpoint.text.toString(),
                deviceToken = token.text.toString(),
                calibrationId = calibration.text.toString(),
                language = selectedLanguage,
            )
        )
        if (!saved) {
            token.setText("")
            status.text = getString(R.string.status_keystore_failed)
            return false
        }
        return true
    }

    private fun startServiceIfConfigured() {
        val config = AppConfigStore.load(this)
        val endpointUri = Uri.parse(config.endpoint)
        val localMock = BuildConfig.DEBUG && endpointUri.scheme == "ws" &&
            endpointUri.host in setOf("127.0.0.1", "localhost", "10.0.2.2")
        val secureEndpoint = endpointUri.scheme == "wss" || localMock
        if (!secureEndpoint || config.deviceToken.isBlank() || config.calibrationId.isBlank()) {
            status.text = getString(R.string.status_need_provisioning)
            return
        }
        requestBatteryExemption()
        val intent = Intent(this, AssistService::class.java).setAction(AssistService.ACTION_START)
        ContextCompat.startForegroundService(this, intent)
        status.text = getString(R.string.status_starting)
    }

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
