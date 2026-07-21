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
import android.view.WindowManager
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.CheckBox
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
    private lateinit var setupPanel: LinearLayout
    private lateinit var toggleSetup: Button
    private lateinit var debugTelemetry: CheckBox

    private val languageTags = SupportedLanguages.all.map { it.tag }
    private var setupExpanded = false

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        when {
            !hasPermission(Manifest.permission.CAMERA) -> {
                setStatus(getString(R.string.status_camera_permission))
            }
            needsNotificationPermission() && !hasPermission(Manifest.permission.POST_NOTIFICATIONS) -> {
                setStatus(getString(R.string.status_notification_permission))
            }
            else -> startServiceIfConfigured()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        // Needs no permission and costs nothing: while this screen is in front, the display will
        // not sleep. That covers the window between pressing Start and the service taking over,
        // which is exactly when a screen timeout would otherwise stop CameraX before the first
        // frame. ScreenKeepAlive's overlay is still what protects the rest of the walk.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        endpoint = findViewById(R.id.endpoint)
        token = findViewById(R.id.token)
        calibration = findViewById(R.id.calibration)
        language = findViewById(R.id.language)
        status = findViewById(R.id.status)
        setupPanel = findViewById(R.id.setupPanel)
        toggleSetup = findViewById(R.id.btnToggleSetup)
        debugTelemetry = findViewById(R.id.debug_telemetry)

        val config = AppConfigStore.load(this)
        endpoint.setText(config.endpoint)
        calibration.setText(config.calibrationId)
        debugTelemetry.isChecked = config.debugTelemetry
        token.apply {
            // Mask bearer tokens; never leave a provisioned secret visible in the form.
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            if (config.deviceToken.isNotBlank()) {
                hint = getString(R.string.hint_token_saved)
            }
        }
        language.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_dropdown_item,
            SupportedLanguages.all.map { getString(it.labelRes) }
        )
        language.setSelection(languageTags.indexOf(config.language).coerceAtLeast(0))

        findViewById<Button>(R.id.btnStart).setOnClickListener { requestAndStart() }
        findViewById<Button>(R.id.btnStop).setOnClickListener {
            val stopIntent = Intent(this, AssistService::class.java).setAction(AssistService.ACTION_STOP)
            startService(stopIntent)
            setStatus(getString(R.string.status_stopped))
        }
        toggleSetup.setOnClickListener { setSetupExpanded(!setupExpanded) }

        val provisioned = config.deviceToken.isNotBlank() &&
            config.calibrationId.isNotBlank() &&
            config.endpoint.isNotBlank()
        setSetupExpanded(!provisioned)
        setStatus(
            if (provisioned) getString(R.string.status_ready) else getString(R.string.status_configure)
        )
    }

    private fun setSetupExpanded(expanded: Boolean) {
        setupExpanded = expanded
        setupPanel.visibility = if (expanded) View.VISIBLE else View.GONE
        val label = if (expanded) R.string.setup_hide else R.string.setup_show
        toggleSetup.setText(label)
        toggleSetup.contentDescription = getString(label)
    }

    private fun setStatus(message: String) {
        // accessibilityLiveRegion=polite on the status view announces changes for TalkBack.
        status.text = message
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
        val typedToken = token.text.toString()
        // Empty field keeps the previously Keystore-saved token; a new value replaces it.
        val deviceToken = typedToken.ifBlank { previous.deviceToken }
        val saved = AppConfigStore.save(
            this,
            previous.copy(
                endpoint = endpoint.text.toString(),
                deviceToken = deviceToken,
                calibrationId = calibration.text.toString(),
                language = selectedLanguage,
                debugTelemetry = debugTelemetry.isChecked,
            )
        )
        if (!saved) {
            token.setText("")
            setStatus(getString(R.string.status_keystore_failed))
            setSetupExpanded(true)
            return false
        }
        // Clear the bearer from the UI after a successful Keystore write.
        token.setText("")
        token.hint = getString(R.string.hint_token_saved)
        return true
    }

    private fun startServiceIfConfigured() {
        val config = AppConfigStore.load(this)
        val endpointDecision = EndpointPolicy.evaluate(
            endpoint = config.endpoint,
            debugBuild = BuildConfig.DEBUG,
            isEmulator = DeviceCapability.isEmulator(),
            allowPhysicalLoopbackDevelopment = BuildConfig.ALLOW_PHYSICAL_LOOPBACK_DEV
        )
        if (!endpointDecision.allowed) {
            setStatus(endpointDecision.message ?: getString(R.string.status_need_provisioning))
            setSetupExpanded(true)
            return
        }
        if (config.deviceToken.isBlank() || config.calibrationId.isBlank()) {
            setStatus(getString(R.string.status_need_provisioning))
            setSetupExpanded(true)
            return
        }
        requestBatteryExemption()
        if (requestOverlayPermissionIfNeeded()) {
            setStatus("Please grant overlay permission and press Start again")
            return
        }
        val intent = Intent(this, AssistService::class.java).setAction(AssistService.ACTION_START)
        ContextCompat.startForegroundService(this, intent)
        setStatus(getString(R.string.status_starting))
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

    /**
     * Open the overlay-permission screen if permission is missing when Start is pressed.
     *
     * Without this permission ScreenKeepAlive cannot hold the display awake, the screen sleeps
     * on its normal timeout, OEM ROMs stop CameraX, and the session dies with no visible cause.
     */
    private fun requestOverlayPermissionIfNeeded(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return false
        if (Settings.canDrawOverlays(this)) return false
        runCatching {
            startActivity(
                Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"))
            )
        }
        return true
    }
}
