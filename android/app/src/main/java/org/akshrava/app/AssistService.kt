package org.akshrava.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.graphics.Color
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.core.app.ServiceCompat
import androidx.lifecycle.LifecycleService
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class AssistService : LifecycleService() {
    companion object {
        const val ACTION_START = "org.akshrava.app.START"
        const val ACTION_STOP = "org.akshrava.app.STOP"
        private const val CHANNEL_ID = "assist-active"
        private const val NOTIFICATION_ID = 1001
        private const val THERMAL_CHECK_INTERVAL_MS = 30_000L
        private const val THERMAL_THROTTLE_C = 43f
        private const val THERMAL_CLEAR_C = 41f
        private const val HEARTBEAT_INTERVAL_MS = 30_000L
    }

    private lateinit var frameExecutor: ExecutorService
    private lateinit var frameEncoder: FrameEncoder
    private lateinit var poseTracker: PoseTracker
    private lateinit var alertManager: AlertManager
    private lateinit var client: ProtocolClient
    private lateinit var calibrationId: String
    private var headsetControls: HeadsetControls? = null
    private var reflexEngine: ReflexEngine = DisabledReflexEngine()
    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private val framePending = AtomicBoolean(false)
    private val lookRequested = AtomicBoolean(false)
    private var frameId = 0L
    private var lastCaptureMs = 0L
    private var lastThermalCheckMs = 0L
    private var lastBatteryWarningMs = 0L
    private var lastCameraUnclearMs = 0L
    private var lastHeartbeatMs = 0L
    private var consecutiveBlurredFrames = 0
    private var previousThumbnail: IntArray? = null
    private val capturePolicy = CapturePolicy()
    @Volatile private var thermalThrottled = false
    @Volatile private var batteryLow = false
    @Volatile private var batteryCritical = false
    @Volatile private var captureSuspendedForBattery = false
    @Volatile private var captureSuspendedForFailure = false
    @Volatile private var quality = Quality()
    @Volatile private var stopping = false

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { stopAssistance(); return Service.START_NOT_STICKY }
            ACTION_START -> if (!::client.isInitialized) startAssistance()
        }
        return Service.START_NOT_STICKY
    }

    private fun startAssistance() {
        val config = AppConfigStore.load(this)
        calibrationId = config.calibrationId
        createChannel()
        startForegroundCompat(notification())
        frameExecutor = Executors.newSingleThreadExecutor()
        frameEncoder = FrameEncoder()
        poseTracker = PoseTracker(this).also { it.start() }
        alertManager = AlertManager(this, config.language)
        val manager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Akshrava:camera").also { it.acquire() }
        client = ProtocolClient(
            endpoint = config.endpoint,
            token = config.deviceToken,
            alertManager = alertManager,
            onState = { status -> updateNotification(status) },
            onFrameSettled = { framePending.set(false) },
            onQuality = { updated ->
                quality = updated
                capturePolicy.quality = updated
            },
            onHighAlert = {
                capturePolicy.markHighAlert(SystemClock.elapsedRealtime())
            }
        )
        client.connect()
        reflexEngine = ReflexFactory.create(this)
        headsetControls = HeadsetControls(
            this,
            onRepeat = { alertManager.repeatLast() },
            onMute = { alertManager.muteFor(15 * 60_000L) },
            onLook = { lookRequested.set(true); alertManager.acknowledgeLook() }
        ).also { it.start() }
        bindCamera()
        SessionFlags.setActive(this, true)
        Watchdog.schedule(this)
        alertManager.status("Assistance started")
    }
    
    private fun bindCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val provider = future.get()
                cameraProvider = provider
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(
                        ResolutionSelector.Builder()
                            .setResolutionStrategy(
                                ResolutionStrategy(
                                    Size(640, 480),
                                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                                )
                            )
                            .build()
                    )
                    // Ask CameraX for the current display orientation before falling back to
                    // FrameEncoder rotation. This avoids the expensive rotate/decode/re-encode
                    // path on devices whose analysis stream can be delivered already oriented.
                    .setTargetRotation(currentDisplayRotation())
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(frameExecutor) { image -> analyzeImage(image) }
                provider.unbindAll()
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, analysis)
            } catch (_: Exception) {
                updateNotification("Rear camera unavailable")
                stopAfterCameraFailure()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyzeImage(image: ImageProxy) {
        try {
            val now = SystemClock.elapsedRealtime()
            maybeCheckThermal(now)
            // Heartbeat means "the camera pipeline is alive", not "a frame was uploaded". It
            // must fire before any of the gates below (battery suspend, pending-frame,
            // duplicate/blur drop) can return early, or a stationary user staring at an
            // unchanging scene -- every frame legitimately a duplicate -- starves the watchdog
            // and gets a loud false "assistance stopped" alarm despite the service running fine.
            maybeHeartbeat(now)

            if (batteryCritical || captureSuspendedForBattery || captureSuspendedForFailure) {
                return
            }
            // Bench mode (vision_enabled=false) or a dead vendor means nothing sent right now
            // would be used. Skip the luma thumbnail, blur/duplicate checks and JPEG encode
            // entirely rather than doing that work and then having sendFrame() discard it --
            // this is heat and battery burned on the donated phones that can least afford it.
            if (!client.canStream()) return

            if (framePending.get()) return
            val priority = lookRequested.getAndSet(false)
            // Headset long-press look or a fresh turn asks for one immediate frame.
            val turning = poseTracker.consumeTurn()
            if (!priority && !turning && now - lastCaptureMs < captureIntervalMs()) return

            val thumbnail = FrameGate.luma(image)
            if (FrameGate.isBlurred(thumbnail)) {
                consecutiveBlurredFrames += 1
                // Blur never drops a frame. Persistent evidence only produces a bounded status
                // prompt, because the cane/guide is primary when the camera cannot be trusted.
                if (consecutiveBlurredFrames >= 5 && now - lastCameraUnclearMs >= 60_000L) {
                    lastCameraUnclearMs = now
                    alertManager.status("Camera view unclear. Use cane or guide.")
                    updateNotification("Camera view unclear")
                }
            } else {
                consecutiveBlurredFrames = 0
            }
            if (!priority && !turning) {
                if (FrameGate.isDuplicate(previousThumbnail, thumbnail)) { previousThumbnail = thumbnail; return }
            }
            // Blur is recorded as a cheap diagnostic signal by FrameGate, but never used to drop
            // a frame: a bad quality estimate must not become a missed-obstacle decision.
            previousThumbnail = thumbnail

            if (!framePending.compareAndSet(false, true)) return
            val frame = frameEncoder.encode(image, quality.maxSide, quality.jpegQ)
            // Fail-closed offline: without licensed TFLite weights, reflex never speaks hazards.
            if (reflexEngine.isArmed()) {
                reflexEngine.evaluate(frame)
            }

            lastCaptureMs = now

            val sent = client.sendFrame(
                ++frameId,
                now,
                poseTracker.snapshot(),
                calibrationId,
                frame,
                mode = if (priority) "priority" else "normal",
                priority = priority
            )
            if (!sent) framePending.set(false)
        } catch (_: Exception) {
            framePending.set(false)
            updateNotification("Camera processing error")
        } finally {
            image.close()
        }
    }

    private fun maybeHeartbeat(now: Long) {
        if (now - lastHeartbeatMs < HEARTBEAT_INTERVAL_MS) return
        lastHeartbeatMs = now
        SessionFlags.heartbeat(this)
    }

    private fun captureIntervalMs(): Long {
        val now = SystemClock.elapsedRealtime()
        return capturePolicy.captureIntervalMs(now, poseTracker.motionState())
    }

    private fun maybeCheckThermal(now: Long) {
        if (now - lastThermalCheckMs < THERMAL_CHECK_INTERVAL_MS) return
        lastThermalCheckMs = now
        val temperature = batteryTemperatureC()
        if (temperature >= THERMAL_THROTTLE_C && !thermalThrottled) {
            thermalThrottled = true
            capturePolicy.thermalThrottled = true
            alertManager.status("Akshrava is running slower to cool down")
        } else if (temperature in 0f..THERMAL_CLEAR_C && thermalThrottled) {
            thermalThrottled = false
            capturePolicy.thermalThrottled = false
        }
        
        // Check battery level
        val status = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = status?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = status?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        if (level > 0 && scale > 0) {
            val batteryPct = level * 100 / scale
            if (batteryPct < 10) {
                if (!batteryCritical) {
                    batteryCritical = true
                    batteryLow = true
                    capturePolicy.batteryLow = true
                    // suspendCaptureForCriticalBattery() speaks its own status message; a
                    // second status() call here would immediately flush (cut off) it, so the
                    // first utterance would never actually be heard.
                    suspendCaptureForCriticalBattery()
                }
            } else if (batteryPct < 15) {
                batteryCritical = false
                if (!batteryLow) {
                    batteryLow = true
                    capturePolicy.batteryLow = true
                    if (now - lastBatteryWarningMs > 120_000L) {
                        alertManager.status("Battery low. Vision alerts may stop soon.")
                        lastBatteryWarningMs = now
                    }
                }
            } else {
                batteryLow = false
                batteryCritical = false
                capturePolicy.batteryLow = false
            }
        }
    }

    private fun batteryTemperatureC(): Float {
        val status = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val tenths = status?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1) ?: -1
        return if (tenths > 0) tenths / 10f else -1f
    }

    private fun suspendCaptureForCriticalBattery() {
        if (captureSuspendedForBattery) return
        // A partial teardown here used to leave sensors registered forever and, worse, left
        // the lateinit `client` "initialized" -- so a later ACTION_START (the user pressing
        // Start again, e.g. after plugging into the power bank) silently no-opped in
        // onStartCommand's `if (!::client.isInitialized)` guard, with no feedback that the
        // press did nothing. Stop the whole service cleanly instead, exactly like a camera
        // failure, so the user gets one consistent "press Start again" recovery path.
        captureSuspendedForBattery = true
        alertManager.status("Battery critical. Vision assistance stopped. Use cane or guide.") {
            ContextCompat.getMainExecutor(this).execute { stopAssistance() }
        }
    }

    private fun stopAfterCameraFailure() {
        if (captureSuspendedForFailure) return
        // A failed bind used to leave the WebSocket, sensors, wake lock and foreground service
        // running indefinitely even though the phone could no longer see. Stop capture now and
        // release the final TTS resource only after the accessibility warning completes.
        captureSuspendedForFailure = true
        SessionFlags.setActive(this, false)
        Watchdog.cancel(this)
        cameraProvider?.unbindAll()
        if (::client.isInitialized) client.close()
        if (::poseTracker.isInitialized) poseTracker.stop()
        if (::frameExecutor.isInitialized) frameExecutor.shutdownNow()
        wakeLock?.let { if (it.isHeld) it.release() }
        alertManager.speakThen("Rear camera unavailable. Use cane or guide.") {
            ContextCompat.getMainExecutor(this).execute { stopAssistance() }
        }
    }

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL_ID, getString(R.string.notification_channel_name), NotificationManager.IMPORTANCE_LOW)
        channel.description = "Visible while camera assistance is active"
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun notification(status: String = getString(R.string.notification_text)): Notification {
        val stopIntent = PendingIntent.getBroadcast(
            this, 0, Intent(this, StopReceiver::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setColor(Color.BLUE)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(status)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(0, getString(R.string.action_stop), stopIntent)
            .build()
    }

    private fun startForegroundCompat(notification: Notification) {
        // FOREGROUND_SERVICE_TYPE_CAMERA and the manifest camera type were introduced in API 30
        // (R), not 29 (Q). Passing it on Q throws (invalid foreground service type) -- and Tier-A
        // devices are floored at Android 10 (Q), so this must gate on R, not Q.
        val serviceType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        } else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, serviceType)
    }

    private fun updateNotification(status: String) {
        if (::client.isInitialized) getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(status))
    }

    private fun stopAssistance() {
        // ACTION_STOP, onDestroy, and a TTS completion callback may all arrive for the same
        // session.  Cleanup includes shutting down TTS, which can complete pending callbacks,
        // so make teardown explicitly idempotent rather than recursively re-entering it.
        if (stopping) return
        stopping = true
        SessionFlags.setActive(this, false)
        Watchdog.cancel(this)
        headsetControls?.stop()
        headsetControls = null
        cameraProvider?.unbindAll()
        if (::client.isInitialized) client.close()
        if (::alertManager.isInitialized) alertManager.shutdown()
        if (::poseTracker.isInitialized) poseTracker.stop()
        if (::frameExecutor.isInitialized) frameExecutor.shutdownNow()
        wakeLock?.let { if (it.isHeld) it.release() }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() { stopAssistance(); super.onDestroy() }
}
