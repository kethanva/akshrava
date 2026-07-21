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
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.util.Size
import okhttp3.OkHttpClient
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
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
import java.util.concurrent.atomic.AtomicInteger

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
        /** Partial wake lock is timed so a hung teardown cannot hold the CPU forever. */
        private const val WAKE_LOCK_TIMEOUT_MS = 60 * 60_000L
        /** Hard upper bound on FGS teardown even if TTS never completes. */
        private const val STOP_HARD_TIMEOUT_MS = 3_000L
        /** Rebind CameraX when analysis callbacks go silent while the session is meant to be live. */
        private const val CAMERA_STALL_REBIND_MS = 15_000L
        private const val CAMERA_STALL_CHECK_MS = 5_000L
    }

    private var frameExecutor: ExecutorService? = null
    private var frameEncoder: FrameEncoder? = null
    private var poseTracker: PoseTracker? = null
    private var alertManager: AlertManager? = null
    private var client: ProtocolClient? = null
    private var http: OkHttpClient? = null
    private var calibrationId: String = ""
    private var headsetControls: HeadsetControls? = null
    private var reflexEngine: ReflexEngine = DisabledReflexEngine()
    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var cameraLifecycleOwner: CameraLifecycleOwner? = null
    private var previewDrain: PreviewSurfaceDrain? = null
    private var screenKeepAlive: ScreenKeepAlive? = null
    private var lastDarkAnnounceMs = 0L
    private var framesAnalyzed = 0L
    private val framePending = AtomicBoolean(false)
    private val lookRequested = AtomicBoolean(false)
    private val bindGeneration = AtomicInteger(0)
    private val mainHandler = Handler(Looper.getMainLooper())
    private var frameId = 0L
    private var lastCaptureMs = 0L
    private var lastThermalCheckMs = 0L
    private var lastBatteryWarningMs = 0L
    private var lastCameraUnclearMs = 0L
    private var lastHeartbeatMs = 0L
    private var lastAnalyzeAtMs = 0L
    private var consecutiveBlurredFrames = 0
    private var previousThumbnail: IntArray? = null
    private val capturePolicy = CapturePolicy()
    private var linkQuality = LinkQualityController()
    @Volatile private var thermalThrottled = false
    @Volatile private var batteryLow = false
    @Volatile private var batteryCritical = false
    @Volatile private var captureSuspendedForBattery = false
    @Volatile private var captureSuspendedForFailure = false
    @Volatile private var quality = Quality()
    @Volatile private var stopping = false
    /** Last analysis target side; rebind when server/link quality crosses a resolution rung. */
    @Volatile private var boundAnalysisMaxSide = 640
    /** Quality rung requested while a frame was in flight; applied on settle. */
    @Volatile private var deferredAnalysisSide: Int? = null

    private val cameraStallCheck = object : Runnable {
        override fun run() {
            if (stopping || client == null) return
            val now = SystemClock.elapsedRealtime()
            val last = lastAnalyzeAtMs
            if (last > 0L && now - last > CAMERA_STALL_REBIND_MS) {
                Log.w("AkshravaDebug", "camera_stall rebind after=${now - last}ms")
                lastAnalyzeAtMs = now
                framePending.set(false)
                deferredAnalysisSide = null
                alertManager?.status("Camera stalled. Recovering.")
                bindCamera()
            }
            mainHandler.postDelayed(this, CAMERA_STALL_CHECK_MS)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { stopAssistance(); return Service.START_NOT_STICKY }
            ACTION_START -> startAssistance()
        }
        return Service.START_NOT_STICKY
    }

    private fun startAssistance() {
        if (stopping) return
        // Pressing Start while a half-dead session is still up used to no-op (client != null),
        // forcing Stop→Start. Rebuild in place so Start itself is the recovery action.
        if (client != null) {
            Log.i("AkshravaDebug", "svc_restart rebuilding live session")
            teardownSessionResources(keepForeground = true)
        }
        stopping = false
        val config = AppConfigStore.load(this)
        // #region agent log
        Log.i("AkshravaDebug", "svc_start endpoint=${config.endpoint} calib=${config.calibrationId} lang=${config.language} hasToken=${config.deviceToken.isNotBlank()}")
        // #endregion
        if (!endpointAllowed(config.endpoint)) {
            stopSelf()
            return
        }
        calibrationId = config.calibrationId
        createChannel()
        startForegroundCompat(notification())
        frameExecutor = Executors.newSingleThreadExecutor()
        frameEncoder = FrameEncoder()
        poseTracker = PoseTracker(this).also { it.start() }
        val am = AlertManager(this, config.language).also { alertManager = it }
        val manager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Akshrava:camera").also {
            it.acquire(WAKE_LOCK_TIMEOUT_MS)
        }
        val httpClient = OkHttpClient.Builder().pingInterval(20, java.util.concurrent.TimeUnit.SECONDS).build().also { http = it }
        // Donated / low-RAM phones start on a cheaper ladder before the first server quality hint.
        linkQuality = LinkQualityController()
        quality = DeviceCapability.initialQuality(this)
        capturePolicy.quality = quality
        capturePolicy.thermalThrottled = thermalThrottled
        capturePolicy.batteryLow = batteryLow
        boundAnalysisMaxSide = analysisTargetSide(quality.maxSide)
        deferredAnalysisSide = null
        framePending.set(false)
        framesAnalyzed = 0L
        frameId = 0L
        lastAnalyzeAtMs = 0L
        lastCaptureMs = 0L
        previousThumbnail = null
        consecutiveBlurredFrames = 0
        captureSuspendedForBattery = false
        captureSuspendedForFailure = false
        val pc = ProtocolClient(
            endpoint = config.endpoint,
            token = config.deviceToken,
            alertManager = am,
            onState = { status -> updateNotification(status) },
            onFrameSettled = { onFrameSlotSettled() },
            onQuality = { updated -> applyEffectiveQuality(linkQuality.onServerQuality(updated)) },
            onHighAlert = {
                capturePolicy.markHighAlert(SystemClock.elapsedRealtime())
            },
            onRoundTripMs = { rtt -> applyEffectiveQuality(linkQuality.onRoundTrip(rtt)) },
            onSettleTimeout = { applyEffectiveQuality(linkQuality.onSettleTimeout()) },
            onResultTelemetry = { telemetry ->
                if (telemetry.lateSuppressed) {
                    Log.i(
                        "AkshravaVision",
                        "result_late_suppressed frame=${telemetry.frameId} " +
                            "detections=${telemetry.detectionCount} labels=${telemetry.labels} " +
                            "age_ms=${telemetry.resultAgeMs}"
                    )
                }
            },
            language = config.language,
            http = httpClient
        ).also { client = it }
        pc.connect()
        reflexEngine = ReflexFactory.create(this)
        headsetControls = HeadsetControls(
            this,
            onRepeat = { am.repeatLast() },
            onMute = { am.muteFor(15 * 60_000L) },
            onLook = { lookRequested.set(true); am.acknowledgeLook() }
        ).also { it.start() }
        screenKeepAlive = ScreenKeepAlive(this).also { it.start() }
        bindCamera()
        SessionFlags.setActive(this, true)
        Watchdog.schedule(this)
        mainHandler.removeCallbacks(cameraStallCheck)
        mainHandler.postDelayed(cameraStallCheck, CAMERA_STALL_CHECK_MS)
        val keepScreenHint = Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
            !android.provider.Settings.canDrawOverlays(this)
        // Without overlay keep-alive, OEM ROMs often kill CameraX after the display sleeps.
        am.status(
            if (keepScreenHint) {
                "Assistance started. Keep the screen on so the camera can see."
            } else {
                "Assistance started"
            }
        )
    }

    /**
     * Drop camera / socket / TTS resources without ending the foreground service.
     * Used when Start is pressed again to recover a stuck live session.
     */
    private fun teardownSessionResources(keepForeground: Boolean) {
        mainHandler.removeCallbacks(cameraStallCheck)
        bindGeneration.incrementAndGet()
        headsetControls?.stop()
        headsetControls = null
        screenKeepAlive?.stop(); screenKeepAlive = null
        cameraProvider?.unbindAll()
        previewDrain?.release(); previewDrain = null
        cameraLifecycleOwner?.destroy(); cameraLifecycleOwner = null
        client?.close(); client = null
        alertManager?.shutdown(); alertManager = null
        poseTracker?.stop(); poseTracker = null
        frameExecutor?.shutdownNow(); frameExecutor = null
        frameEncoder = null
        wakeLock?.let { if (it.isHeld) it.release(); wakeLock = null }
        http = null
        framePending.set(false)
        deferredAnalysisSide = null
        if (!keepForeground) {
            SessionFlags.setActive(this, false)
            Watchdog.cancel(this)
        }
    }

    private fun endpointAllowed(endpoint: String): Boolean {
        return EndpointPolicy.evaluate(
            endpoint = endpoint,
            debugBuild = BuildConfig.DEBUG,
            isEmulator = DeviceCapability.isEmulator(),
            allowPhysicalLoopbackDevelopment = BuildConfig.ALLOW_PHYSICAL_LOOPBACK_DEV
        ).allowed
    }
    
    private fun bindCamera() {
        val generation = bindGeneration.incrementAndGet()
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                if (stopping || generation != bindGeneration.get()) {
                    // Bind completed after stop: never attach an analyzer to a shutdown executor.
                    runCatching {
                        val provider = future.get()
                        provider.unbindAll()
                    }
                    return@addListener
                }
                val provider = future.get()
                if (stopping || generation != bindGeneration.get()) {
                    provider.unbindAll()
                    return@addListener
                }
                cameraProvider = provider
                val analysisSide = analysisTargetSide(quality.maxSide)
                boundAnalysisMaxSide = analysisSide
                val rotation = currentDisplayRotation()
                // Some OEMs (incl. OnePlus) deliver black / zero ImageAnalysis frames unless a
                // Preview use-case is also bound. Drain Preview via ImageReader so the capture
                // session is not stalled by an undrained SurfaceTexture buffer queue.
                previewDrain?.release()
                cameraLifecycleOwner?.destroy()
                val owner = CameraLifecycleOwner().also {
                    it.resume()
                    cameraLifecycleOwner = it
                }
                val drain = PreviewSurfaceDrain().also { previewDrain = it }
                val preview = Preview.Builder()
                    .setTargetRotation(rotation)
                    .build()
                drain.attach(preview)
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(
                        ResolutionSelector.Builder()
                            .setResolutionStrategy(
                                ResolutionStrategy(
                                    Size(analysisSide, analysisSide * 3 / 4),
                                    ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER
                                )
                            )
                            .build()
                    )
                    // Ask CameraX for the current display orientation before falling back to
                    // FrameEncoder rotation. This avoids the expensive rotate/decode/re-encode
                    // path on devices whose analysis stream can be delivered already oriented.
                    .setTargetRotation(rotation)
                    // Continuous capture path: drop oldest, keep latest under backlog.
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                val exec = frameExecutor
                if (stopping || generation != bindGeneration.get() || exec == null || exec.isShutdown) {
                    provider.unbindAll()
                    previewDrain?.release(); previewDrain = null
                    cameraLifecycleOwner?.destroy(); cameraLifecycleOwner = null
                    return@addListener
                }
                analysis.setAnalyzer(exec) { image -> analyzeImage(image) }
                provider.unbindAll()
                provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                // #region agent log
                Log.i("AkshravaDebug", "camera_bound ok rotation=$rotation analysisSide=$analysisSide")
                // #endregion
                Log.i("AkshravaVision", "camera bound preview+analysis rotation=$rotation")
            } catch (ex: Exception) {
                Log.e("AkshravaVision", "camera bind failed", ex)
                previewDrain?.release(); previewDrain = null
                cameraLifecycleOwner?.destroy(); cameraLifecycleOwner = null
                if (!stopping) {
                    updateNotification("Rear camera unavailable")
                    stopAfterCameraFailure()
                }
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyzeImage(image: ImageProxy) {
        var closed = false
        try {
            framesAnalyzed += 1
            val now = SystemClock.elapsedRealtime()
            lastAnalyzeAtMs = now
            if (framesAnalyzed == 1L || framesAnalyzed % 30L == 0L) {
                Log.i("AkshravaVision", "analyze frames=$framesAnalyzed ${image.width}x${image.height}")
            }
            maybeCheckThermal(now)
            // Heartbeat means "the camera pipeline is alive", not "a frame was uploaded". It
            // must fire before any of the gates below (battery suspend, pending-frame,
            // duplicate/blur drop) can return early, or a stationary user staring at an
            // unchanging scene -- every frame legitimately a duplicate -- starves the watchdog
            // and gets a loud false "assistance stopped" alarm despite the service running fine.
            maybeHeartbeat(now)

            if (batteryCritical || captureSuspendedForBattery || captureSuspendedForFailure) {
                // #region agent log
                if (framesAnalyzed <= 5L || framesAnalyzed % 30L == 0L) {
                    Log.i("AkshravaDebug", "frame_drop suspended battCrit=$batteryCritical suspBatt=$captureSuspendedForBattery suspFail=$captureSuspendedForFailure n=$framesAnalyzed")
                }
                // #endregion
                return
            }
            // Bench mode (vision_enabled=false) or a dead vendor means nothing sent right now
            // would be used. Skip the luma thumbnail, blur/duplicate checks and JPEG encode
            // entirely rather than doing that work and then having sendFrame() discard it --
            // this is heat and battery burned on the donated phones that can least afford it.
            val currentClient = client
            if (currentClient == null || !currentClient.canStream()) {
                // #region agent log
                if (framesAnalyzed <= 5L || framesAnalyzed % 30L == 0L) {
                    Log.i("AkshravaDebug", "frame_drop canStream=false clientNull=${currentClient == null} n=$framesAnalyzed")
                }
                // #endregion
                return
            }

            // One encode/upload at a time. CameraX KEEP_ONLY_LATEST already sheds older
            // buffers; this flag also stops us from racing the WebSocket in-flight slot.
            if (!framePending.compareAndSet(false, true)) {
                // #region agent log
                if (framesAnalyzed <= 5L) Log.i("AkshravaDebug", "frame_drop framePending stuck n=$framesAnalyzed")
                // #endregion
                return
            }
            val priority = lookRequested.getAndSet(false)
            // Headset long-press look or a fresh turn asks for one immediate frame.
            val turning = poseTracker?.consumeTurn() ?: false
            if (!priority && !turning && now - lastCaptureMs < captureIntervalMs()) {
                framePending.set(false)
                return
            }

            val thumbnail = FrameGate.luma(image)
            // #region agent log
            if (framesAnalyzed <= 5L) {
                val avgLuma = if (thumbnail.isNotEmpty()) thumbnail.sum() / thumbnail.size else 0
                Log.i("AkshravaDebug", "frame_luma n=$framesAnalyzed avgLuma=$avgLuma nearBlack=${FrameGate.isNearBlack(thumbnail)}")
            }
            // #endregion
            if (FrameGate.isNearBlack(thumbnail)) {
                framePending.set(false)
                if (now - lastDarkAnnounceMs > 8_000L) {
                    lastDarkAnnounceMs = now
                    updateNotification("Camera is dark — uncover rear lens")
                    alertManager?.status("Camera is dark. Uncover the rear lens.")
                }
                // Never upload black OEM buffers — YOLO returns empty and burns RTT budget.
                return
            }
            if (FrameGate.isBlurred(thumbnail)) {
                consecutiveBlurredFrames += 1
                // Blur never drops a frame. Persistent evidence only produces a bounded status
                // prompt, because the cane/guide is primary when the camera cannot be trusted.
                if (consecutiveBlurredFrames >= 5 && now - lastCameraUnclearMs >= 60_000L) {
                    lastCameraUnclearMs = now
                    alertManager?.status("Camera view unclear. Use cane or guide.")
                    updateNotification("Camera view unclear")
                }
            } else {
                consecutiveBlurredFrames = 0
            }
            // Do NOT drop near-duplicate frames after the capture interval has elapsed.
            // Hazard S2 requires tracker hits >= 2; a still scene of a person/vehicle is
            // intentionally re-sampled and must reach the cloud so the second hit can fire.
            // Burst-only duplicate suppression: same-interval accidental double analyze.
            if (!priority && !turning && now - lastCaptureMs < 350L) {
                if (FrameGate.isDuplicate(previousThumbnail, thumbnail)) {
                    previousThumbnail = thumbnail
                    framePending.set(false)
                    return
                }
            }
            // Blur is recorded as a cheap diagnostic signal by FrameGate, but never used to drop
            // a frame: a bad quality estimate must not become a missed-obstacle decision.
            previousThumbnail = thumbnail

            val encoder = frameEncoder
            if (encoder == null) {
                framePending.set(false)
                // Leave the close to `finally` — closing here as well double-closed the ImageProxy.
                return
            }
            val prepared = encoder.prepare(image, quality.maxSide)
            image.close()
            closed = true
            val frame = encoder.compressPrepared(prepared, quality.jpegQ)
            // Fail-closed offline: without licensed TFLite weights, reflex never speaks hazards.
            if (reflexEngine.isArmed()) {
                reflexEngine.evaluate(frame)
            }

            lastCaptureMs = now

            val poseSnapshot = poseTracker?.snapshot()
            val sent = currentClient.sendFrame(
                ++frameId,
                now,
                poseSnapshot,
                calibrationId,
                frame,
                mode = if (priority) "priority" else "normal",
                priority = priority
            )
            // #region agent log
            if (frameId <= 5L || frameId % 10L == 0L) {
                Log.i("AkshravaDebug", "frame_sent id=$frameId sent=$sent size=${frame.jpeg.size}")
            }
            // #endregion
            if (!sent) framePending.set(false)
        } catch (ex: Exception) {
            // Log before recovering — silent failures in the analysis loop are dangerous
            // on a safety-critical system and produce no diagnostic output otherwise.
            Log.e("AkshravaVision", "analyzeImage error (frames=$framesAnalyzed)", ex)
            framePending.set(false)
            updateNotification("Camera processing error")
        } finally {
            if (!closed) image.close()
        }
    }

    private fun onFrameSlotSettled() {
        framePending.set(false)
        val deferred = deferredAnalysisSide ?: return
        if (stopping || client == null) return
        if (deferred != boundAnalysisMaxSide) {
            scheduleCameraRebind(deferred)
        } else {
            deferredAnalysisSide = null
        }
    }

    private fun applyEffectiveQuality(updated: Quality) {
        quality = updated
        capturePolicy.quality = updated
        val target = analysisTargetSide(updated.maxSide)
        if (target == boundAnalysisMaxSide || stopping || client == null) return
        // Rebinding CameraX mid-upload races the one-in-flight slot and can desync the
        // header/JPEG pair on the server (protocol_violation / soft rejects). Wait for settle.
        if (framePending.get()) {
            deferredAnalysisSide = target
            Log.i("AkshravaDebug", "camera_rebind_deferred target=$target")
            return
        }
        scheduleCameraRebind(target)
    }

    private fun scheduleCameraRebind(target: Int) {
        boundAnalysisMaxSide = target
        deferredAnalysisSide = null
        ContextCompat.getMainExecutor(this).execute {
            if (!stopping) bindCamera()
        }
    }

    /** CameraX analysis ladder aligned with protocol max_side rungs (not every JPEG q step). */
    private fun analysisTargetSide(maxSide: Int): Int {
        val uncapped = when {
            maxSide <= 320 -> 320
            maxSide <= 384 -> 384
            maxSide <= 480 -> 480
            maxSide <= 512 -> 512
            else -> 640
        }
        return minOf(uncapped, DeviceCapability.analysisSideCap(this))
    }

    private fun maybeHeartbeat(now: Long) {
        if (now - lastHeartbeatMs < HEARTBEAT_INTERVAL_MS) return
        lastHeartbeatMs = now
        SessionFlags.heartbeat(this)
    }

    private fun captureIntervalMs(): Long {
        val now = SystemClock.elapsedRealtime()
        val motion: MotionState = poseTracker?.motionState() ?: MotionState.STATIONARY
        return capturePolicy.captureIntervalMs(now, motion)
    }

    private fun maybeCheckThermal(now: Long) {
        if (now - lastThermalCheckMs < THERMAL_CHECK_INTERVAL_MS) return
        lastThermalCheckMs = now
        // Query the sticky battery broadcast once and share the result for both thermal and
        // battery-level checks, avoiding two binder calls per thermal interval.
        val batteryStatus = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val temperature = batteryTemperatureC(batteryStatus)
        if (temperature >= THERMAL_THROTTLE_C && !thermalThrottled) {
            thermalThrottled = true
            capturePolicy.thermalThrottled = true
            alertManager?.status("Akshrava is running slower to cool down")
        } else if (temperature in 0f..THERMAL_CLEAR_C && thermalThrottled) {
            thermalThrottled = false
            capturePolicy.thermalThrottled = false
        }
        
        // Check battery level
        val level = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
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
                        alertManager?.status("Battery low. Vision alerts may stop soon.")
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

    private fun batteryTemperatureC(batteryStatus: android.content.Intent?): Float {
        val tenths = batteryStatus?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1) ?: -1
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
        alertManager?.status("Battery critical. Vision assistance stopped. Use cane or guide.") {
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
        previewDrain?.release(); previewDrain = null
        cameraLifecycleOwner?.destroy(); cameraLifecycleOwner = null
        client?.close(); client = null
        poseTracker?.stop(); poseTracker = null
        frameExecutor?.shutdownNow(); frameExecutor = null
        wakeLock?.let { if (it.isHeld) it.release(); wakeLock = null }
        alertManager?.speakThen("Rear camera unavailable. Use cane or guide.") {
            ContextCompat.getMainExecutor(this).execute { stopAssistance() }
        }
        // Hard timeout so a stuck TTS callback cannot leave the FGS running forever.
        mainHandler.postDelayed({ stopAssistance() }, STOP_HARD_TIMEOUT_MS)
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
        // FOREGROUND_SERVICE_TYPE_CAMERA requires API 30+. On API 26–29 (supported donated
        // cohort below R) start a plain FGS; camera permission still gates capture.
        val serviceType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
        } else 0
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification, serviceType)
    }

    private fun updateNotification(status: String) {
        if (client != null) getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(status))
    }

    private fun stopAssistance() {
        // ACTION_STOP, onDestroy, and a TTS completion callback may all arrive for the same
        // session.  Cleanup includes shutting down TTS, which can complete pending callbacks,
        // so make teardown explicitly idempotent rather than recursively re-entering it.
        if (stopping) return
        stopping = true
        mainHandler.removeCallbacksAndMessages(null)
        SessionFlags.setActive(this, false)
        Watchdog.cancel(this)
        teardownSessionResources(keepForeground = false)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() { stopAssistance(); super.onDestroy() }
}
