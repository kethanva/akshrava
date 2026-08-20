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
import android.os.IBinder
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
        /** Consecutive black frames before the "uncover the lens" prompt is spoken (F-02). */
        internal const val OCCLUDED_FRAMES_BEFORE_ANNOUNCE = 3
        private const val THERMAL_THROTTLE_C = 43f
        private const val THERMAL_CLEAR_C = 41f
        internal const val HEARTBEAT_INTERVAL_MS = 30_000L
        /** Partial wake lock is timed so a hung teardown cannot hold the CPU forever. */
        internal const val WAKE_LOCK_TIMEOUT_MS = 60 * 60_000L

        /**
         * How often a live session re-arms its timed wake locks.
         *
         * The timeout above is a safety net against a hung teardown, not a session budget: a walk
         * longer than an hour is ordinary use, and when the timeout fired mid-walk the CPU lock
         * and (without overlay permission) the screen-bright lock were both silently lost. The
         * display then sleeps on its normal timeout, OEM ROMs stop delivering CameraX frames, and
         * assistance ends with the socket still open and nothing in any log to explain it.
         * Re-arming well inside the window keeps a healthy session alive indefinitely while a
         * session that has stopped analysing frames still lets the locks lapse on schedule.
         */
        internal const val WAKE_LOCK_RENEW_INTERVAL_MS = 15 * 60_000L
        /** Hard upper bound on FGS teardown even if TTS never completes. */
        private const val STOP_HARD_TIMEOUT_MS = 3_000L
        /** Rebind CameraX when analysis callbacks go silent while the session is meant to be live. */
        internal const val CAMERA_STALL_REBIND_MS = 15_000L
        internal const val CAMERA_STALL_CHECK_MS = 5_000L
        /** Floor between notification re-posts; results arrive far faster than this is useful. */
        private const val MIN_NOTIFICATION_INTERVAL_MS = 1_000L
        /** Floor between quality-driven camera rebinds. Stall recovery bypasses this. */
        internal const val MIN_QUALITY_REBIND_INTERVAL_MS = 10_000L

        /**
         * Age at which a held in-flight frame slot is treated as wedged rather than merely busy.
         *
         * Comfortably past ProtocolClient.FRAME_SETTLE_TIMEOUT_MS (10 s), which is supposed to
         * release the slot even when a result never arrives -- so crossing this line means that
         * safety net did not fire and no further frame will ever be sent.
         */
        internal const val FRAME_SLOT_WEDGED_MS = 15_000L

        /**
         * True when the analyzer has gone silent long enough to justify rebinding CameraX.
         *
         * Extracted as a pure function so the rule is unit-testable without a camera, a service,
         * or a Handler -- the same treatment [FrameGate.shouldAnnounceBlur] and
         * [ProtocolClient.shouldTickStaleInference] already get.
         *
         * The `lastAnalyzeAtMs > 0` term means "the camera is not bound yet", NOT "no frame has
         * arrived yet". bindCamera() baselines the clock on a successful bind, so a HAL that
         * accepts the configuration and then never delivers a frame is treated as a stall like
         * any other silence. Before a bind there is nothing to recover, so 0 still means quiet.
         */
        internal fun shouldRebindForStall(nowMs: Long, lastAnalyzeAtMs: Long): Boolean =
            lastAnalyzeAtMs > 0L && nowMs - lastAnalyzeAtMs > CAMERA_STALL_REBIND_MS

        /**
         * Duplicate START on a healthy session must be ignored. A terminal client (revoked,
         * token rejected, session taken over) is still non-null, so Start must rebuild.
         */
        internal fun ignoresDuplicateStart(
            stopping: Boolean,
            hasClient: Boolean,
            hasAlertManager: Boolean,
            clientTerminal: Boolean
        ): Boolean =
            !stopping && !(hasClient && clientTerminal) && hasClient && hasAlertManager

        internal const val ANALYZE_FAILURES_BEFORE_STOP = 5
        internal const val ANALYZE_FAILURE_ANNOUNCE_COOLDOWN_MS = 8_000L

        internal fun shouldAnnounceAnalyzeFailure(
            nowMs: Long,
            consecutiveFailures: Int,
            lastAnnounceMs: Long
        ): Boolean {
            if (consecutiveFailures < 2) return false
            if (lastAnnounceMs == 0L) return true
            return nowMs - lastAnnounceMs >= ANALYZE_FAILURE_ANNOUNCE_COOLDOWN_MS
        }
    }

    private var frameExecutor: ExecutorService? = null
    @Volatile private var frameEncoder: FrameEncoder? = null
    @Volatile private var poseTracker: PoseTracker? = null
    @Volatile private var alertManager: AlertManager? = null
    @Volatile private var client: ProtocolClient? = null
    private var http: OkHttpClient? = null
    private var calibrationId: String = ""
    private var headsetControls: HeadsetControls? = null
    private var gestureDetectorEngine: GestureDetectorEngine? = null
    private var ambientLightMonitor: AmbientLightMonitor? = null
    private var reflexEngine: ReflexEngine = DisabledReflexEngine()
    private var wakeLock: PowerManager.WakeLock? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var cameraLifecycleOwner: CameraLifecycleOwner? = null
    private var previewDrain: PreviewSurfaceDrain? = null
    private var screenKeepAlive: ScreenKeepAlive? = null
    private var osLifecycleReceiver: android.content.BroadcastReceiver? = null
    private var lastDarkAnnounceMs = 0L
    private var lastTiltAnnounceMs = 0L
    private var lastGlareAnnounceMs = 0L
    private var extremeTiltSinceMs: Long? = null
    private var framesAnalyzed = 0L
    private val framePending = AtomicBoolean(false)
    private val frameSlotLock = Any()

    /**
     * When the current in-flight slot was claimed, or 0 when free.
     *
     * Shedding frames because one is already in flight is the normal steady state -- the camera
     * analyses at ~30 fps and the server admits 1.2 fps, so ~29 of every 30 frames are dropped
     * here and the existing "framePending stuck" line fires constantly during perfectly healthy
     * operation. That makes it useless for spotting the failure that looks identical from the
     * outside: a slot that is never released, which silently ends the session while the camera
     * keeps running and the socket stays open (so neither the stall detector nor a drop fires).
     *
     * ProtocolClient arms a [ProtocolClient.FRAME_SETTLE_TIMEOUT_MS] timeout on every send, so a
     * slot older than that plus a margin means the timeout machinery itself failed to fire, which
     * is the only shape of this bug worth waking anyone up for.
     */
    @Volatile private var framePendingSinceMs = 0L
    private val lookRequested = AtomicBoolean(false)
    private val bindGeneration = AtomicInteger(0)
    private val mainHandler = Handler(Looper.getMainLooper())
    private var frameId = 0L
    private var lastCaptureMs = 0L
    private var lastThermalCheckMs = 0L
    private var lastBatteryWarningMs = 0L
    /** null = the wipe-lens prompt has not spoken this session; see FrameGate.shouldAnnounceBlur. */
    private var lastCameraUnclearMs: Long? = null
    private var lastHeartbeatMs = 0L
    private var lastWakeLockRenewAtMs = 0L
    @Volatile private var wakeKeepAliveWarningAnnounced = false
    /**
     * When the analyzer last ran, in [SystemClock.elapsedRealtime].
     *
     * Volatile because this is genuinely cross-thread: it is written from the CameraX analyzer on
     * [frameExecutor] and both read and written from [cameraStallCheck] on the main thread. There
     * is no happens-before edge between those two, so without this the stall detector could read
     * an arbitrarily stale value -- rebinding a healthy camera (a 1-2 s detection blackout the
     * user experiences as intermittent failure) or failing to rebind a dead one.
     *
     * The other frame-pipeline counters nearby are safe without it: they are reset on the main
     * thread in startAssistance() *before* setAnalyzer() hands them to the executor, and
     * submitting to an executor establishes happens-before. Only these two escape that pattern.
     */
    @Volatile private var lastAnalyzeAtMs = 0L

    /**
     * Volatile for the same reason: [scheduleCameraRebind] is reached from ProtocolClient
     * callbacks, which run on the OkHttp listener thread (onQuality / onRoundTripMs / onFrameSettled)
     * and on the reconnect scheduler thread (onSettleTimeout) -- two different non-main threads
     * doing read-modify-write on this rebind cooldown. [LinkQualityController] already marks all
     * of its cross-thread state volatile; these two fields were the inconsistency.
     */
    @Volatile private var lastQualityRebindAtMs = 0L
    private var lastNotificationText: String? = null
    private var lastNotificationAtMs = 0L
    private var queuedNotificationText: String? = null
    private val notificationFlush = Runnable { flushNotification() }
    /**
     * Hard-timeout stop after camera failure. Checks [assistanceGeneration] so a Start that
     * bumped the generation inside the timeout window is not torn down by this deferred stop.
     */
    private val stopHardTimeout = Runnable {
        if (assistanceGeneration.get() == stopHardTimeoutGeneration) stopAssistance()
    }
    private var consecutiveBlurredFrames = 0
    private var consecutiveOccludedFrames = 0
    private var consecutiveGlaredFrames = 0
    private var consecutiveAnalyzeFailures = 0
    private var lastAnalyzeFailureAnnounceMs = 0L
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
    /** Bumped on every Start so deferred camera-failure stops cannot kill a rebuilt session. */
    private val assistanceGeneration = AtomicInteger(0)
    private var stopHardTimeoutGeneration = 0
    /** Last analysis target side; rebind when server/link quality crosses a resolution rung. */
    @Volatile private var boundAnalysisMaxSide = 640
    /** Quality rung requested while a frame was in flight; applied on settle. */
    @Volatile private var deferredAnalysisSide: Int? = null

    private val cameraStallCheck = object : Runnable {
        override fun run() {
            // Only `stopping` ends the loop. Returning early on a transient `client == null`
            // skipped the re-post at the bottom, so the stall detector died permanently the
            // first time it happened to run mid-teardown — and from then on a camera that went
            // silent was never rebound and the session was over until a manual Stop/Start.
            if (stopping) return
            if (client != null) {
                val now = SystemClock.elapsedRealtime()
                val last = lastAnalyzeAtMs
                if (shouldRebindForStall(now, last)) {
                    Log.w("AkshravaDebug", "camera_stall rebind after=${now - last}ms")
                    lastAnalyzeAtMs = now
                    deferredAnalysisSide = null
                    alertManager?.statusKey("op_camera_stalled")
                    bindCamera()
                }
            }
            mainHandler.postDelayed(this, CAMERA_STALL_CHECK_MS)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // LifecycleService dispatches its lifecycle events from super.onStartCommand; skipping it
        // leaves this service's own LifecycleOwner stuck and silently breaks anything that
        // observes it. (Camera binding uses the separate CameraLifecycleOwner, which is why the
        // omission was invisible until lint flagged it.)
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_STOP -> { stopAssistance(); return Service.START_NOT_STICKY }
            ACTION_START -> startAssistance()
        }
        return Service.START_NOT_STICKY
    }

    private fun startAssistance() {
        // Cancel a deferred stop from stopAfterCameraFailure so a real Start inside the
        // hard-timeout window is not immediately torn down by the pending runnable.
        mainHandler.removeCallbacks(stopHardTimeout)

        // Healthy live session: ignore duplicate START (re-taps, OEM intent redelivery,
        // accidental second presses after the battery/overlay settings screen). Rebuilding
        // here closed the WSS, reset the cloud tracker, and briefly set canStream=false —
        // the soak run showed svc_restart at ~96s with client=true while results were still
        // flowing, which is exactly the mid-walk "assistance died then came back" flap.
        // A terminally-dead client (device revoked, token rejected, session taken over —
        // handlePermanentFailure stops the reconnect executor) is still non-null, so a plain
        // non-null check here would swallow the Start press that is the user's ONLY way back.
        if (ignoresDuplicateStart(
                stopping = stopping,
                hasClient = client != null,
                hasAlertManager = alertManager != null,
                clientTerminal = client?.isTerminal() == true
            )
        ) {
            Log.i("AkshravaDebug", "svc_start ignored; session already active")
            // #region agent log
            AgentDebugLog.log(
                "H1",
                "AssistService.startAssistance:entry",
                "svc_start_ignored_active",
                mapOf("client" to true, "alertManager" to true)
            )
            // #endregion
            return
        }

        // Bump generation so any TTS completion callback still in flight (AlertManager.shutdown
        // re-posts them) becomes a no-op against this new session.
        val activeGeneration = assistanceGeneration.incrementAndGet()
        if (stopping || client != null || alertManager != null) {
            // stopping: interrupt in-progress Stop. client XOR alertManager: rebuild a half-dead
            // or camera-failure leftover so Start itself is the recovery action (and so we do
            // not leak the previous socket / wake lock / TTS engine).
            Log.i(
                "AkshravaDebug",
                "svc_restart rebuilding session stopping=$stopping " +
                    "client=${client != null} alertManager=${alertManager != null}"
            )
            teardownSessionResources(keepForeground = true)
        }
        stopping = false
        val config = AppConfigStore.load(this)
        AgentDebugLog.bind(this, config.debugTelemetry)
        Log.i(
            "AkshravaDebug",
            "svc_start endpoint_class=${EndpointPolicy.classify(config.endpoint).logValue} " +
                "calib_set=${config.calibrationId.isNotBlank()} lang=${config.language} " +
                "hasToken=${config.deviceToken.isNotBlank()}"
        )
        // #region agent log
        AgentDebugLog.log(
            "H1",
            "AssistService.startAssistance:entry",
            "svc_start",
            mapOf(
                "hasToken" to config.deviceToken.isNotBlank(),
                "overlayAllowed" to (
                    android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.M ||
                        android.provider.Settings.canDrawOverlays(this)
                    )
            )
        )
        // #endregion
        if (!endpointAllowed(config.endpoint)) {
            Log.e("AkshravaVision", "assistance_start_rejected reason=invalid_endpoint")
            stopSelf()
            return
        }
        if (!config.hasRequiredProvisioning()) {
            Log.e("AkshravaVision", "assistance_start_rejected reason=incomplete_provisioning")
            stopSelf()
            return
        }
        calibrationId = config.calibrationId
        createChannel()
        startForegroundCompat(notification())
        // Fresh session: never let the previous run's text suppress the first real status.
        lastNotificationText = null
        lastNotificationAtMs = 0L
        queuedNotificationText = null
        frameExecutor = Executors.newSingleThreadExecutor()
        frameEncoder = FrameEncoder()
        poseTracker = PoseTracker(this).also { it.start() }
        val am = AlertManager(this, config.language).also { alertManager = it }
        val manager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = runCatching {
            manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Akshrava:camera").also {
                // Not reference counted: the session re-acquires this lock periodically to re-arm its
                // timeout (see maybeRenewWakeLocks). With the default counting behaviour each renewal
                // would add a hold that the single release() in teardown could never balance, leaking
                // the CPU lock for the life of the process.
                it.setReferenceCounted(false)
                it.acquire(WAKE_LOCK_TIMEOUT_MS)
            }
        }.onFailure {
            Log.e("AkshravaVision", "CPU wake-lock acquisition failed", it)
        }.getOrNull()
        lastWakeLockRenewAtMs = SystemClock.elapsedRealtime()
        val httpClient = OkHttpClient.Builder().pingInterval(20, java.util.concurrent.TimeUnit.SECONDS).build().also { http = it }
        // Donated / low-RAM phones start on a cheaper ladder before the first server quality hint.
        linkQuality = LinkQualityController()
        quality = DeviceCapability.initialQuality(this)
        capturePolicy.quality = quality
        capturePolicy.thermalThrottled = thermalThrottled
        capturePolicy.batteryLow = batteryLow
        boundAnalysisMaxSide = analysisTargetSide(quality.maxSide)
        deferredAnalysisSide = null
        resetFrameSlot()
        framesAnalyzed = 0L
        frameId = 0L
        lastAnalyzeAtMs = 0L
        lastCaptureMs = 0L
        previousThumbnail = null
        lookRequested.set(false)
        consecutiveBlurredFrames = 0
        consecutiveOccludedFrames = 0
        consecutiveGlaredFrames = 0
        consecutiveAnalyzeFailures = 0
        lastAnalyzeFailureAnnounceMs = 0L
        lastCameraUnclearMs = null
        extremeTiltSinceMs = null
        captureSuspendedForBattery = false
        captureSuspendedForFailure = false
        val pc = ProtocolClient(
            endpoint = config.endpoint,
            token = config.deviceToken,
            alertManager = am,
            onState = { status ->
                if (isCurrentAssistance(activeGeneration)) updateNotification(status)
            },
            onFrameSettled = { onFrameSlotSettled(activeGeneration) },
            onQuality = { updated ->
                if (isCurrentAssistance(activeGeneration)) {
                    applyEffectiveQuality(linkQuality.onServerQuality(updated))
                }
            },
            onHighAlert = {
                if (isCurrentAssistance(activeGeneration)) {
                    capturePolicy.markHighAlert(SystemClock.elapsedRealtime())
                }
            },
            onRoundTripMs = { rtt ->
                if (isCurrentAssistance(activeGeneration)) {
                    applyEffectiveQuality(linkQuality.onRoundTrip(rtt))
                }
            },
            onSettleTimeout = {
                if (isCurrentAssistance(activeGeneration)) {
                    applyEffectiveQuality(linkQuality.onSettleTimeout())
                }
            },
            onResultTelemetry = { telemetry ->
                if (isCurrentAssistance(activeGeneration) && telemetry.lateSuppressed) {
                    Log.i(
                        "AkshravaVision",
                        "result_late_suppressed frame=${telemetry.frameId} " +
                            "detections=${telemetry.detectionCount} labels=${telemetry.labels} " +
                            "age_ms=${telemetry.resultAgeMs}"
                    )
                }
            },
            onTerminal = { key ->
                ContextCompat.getMainExecutor(this).execute {
                    if (isCurrentAssistance(activeGeneration)) {
                        stopAfterUnrecoverableFailure(key, "Session ended")
                    }
                }
            },
            language = config.language,
            http = httpClient,
            debugTelemetry = config.debugTelemetry
        ).also { client = it }
        pc.connect()
        reflexEngine = ReflexFactory.create(this)
        headsetControls = HeadsetControls(
            this,
            onRepeat = {
                if (isCurrentAssistance(activeGeneration)) am.repeatLast()
            },
            onMute = {
                if (isCurrentAssistance(activeGeneration)) am.toggleMute()
            },
            onLook = {
                if (isCurrentAssistance(activeGeneration)) {
                    lookRequested.set(true)
                    am.acknowledgeLook()
                }
            },
            // Earbuds died or the cable was pulled (F-17). Say so and keep going: silence here
            // would be indistinguishable from a dead app to someone who cannot see the screen.
            onAudioRouteLost = {
                if (isCurrentAssistance(activeGeneration)) {
                    am.statusKey("op_headset_disconnected")
                }
            }
        ).also { it.start() }
        gestureDetectorEngine = GestureDetectorEngine(
            // Double shake = one immediate look (F-31), nothing else. Speaking anything additional
            // here would flush an in-progress hazard alert, and this gesture can fire by accident.
            onDoubleShake = {
                // Samples arrive on the sensor thread; acknowledgeLook vibrates and speaks, and
                // blocking there stalls delivery for PoseTracker, which shares the registration.
                mainHandler.post {
                    if (isCurrentAssistance(activeGeneration)) {
                        lookRequested.set(true)
                        am.acknowledgeLook()
                    }
                }
            }
        ).also { engine ->
            // Driven from PoseTracker's existing accelerometer stream rather than a second
            // listener on the same sensor.
            poseTracker?.onAccelerometerSample = { x, y, z, nowMs ->
                engine.onAccelerometerSample(x, y, z, nowMs)
            }
        }
        ambientLightMonitor = AmbientLightMonitor(this) { level ->
            // Samples arrive on the sensor thread; status() speaks, so hop off it for the same
            // reason the gesture engine does — PoseTracker shares that thread.
            mainHandler.post {
                if (isCurrentAssistance(activeGeneration)) announceAmbientLightEdge(am, level)
            }
        }.also { monitor ->
            // A phone with no light sensor simply never gets this context. It is additive
            // awareness, so its absence is logged, not spoken: it is not a fault the user can act on.
            if (!monitor.start()) Log.i("AkshravaDebug", "ambient_light_sensor_unavailable")
        }
        // A session only survives a long walk if the display stays awake: many OEM ROMs stop
        // delivering CameraX frames once the screen sleeps, which ends the walk silently. The
        // overlay is preferred and the timed screen wake-lock is the fallback. Treat failure of
        // both as a first-class warning rather than a detail.
        val holdingScreenOn = ScreenKeepAlive(this).also { screenKeepAlive = it }.start()
        val holdingCpuAwake = wakeLock?.isHeld == true
        wakeKeepAliveWarningAnnounced = !holdingScreenOn || !holdingCpuAwake
        // #region agent log
        AgentDebugLog.log(
            "H1",
            "AssistService.startAssistance:screenKeepAlive",
            "screen_keep_alive",
            mapOf(
                "holdingScreenOn" to holdingScreenOn,
                "mode" to (screenKeepAlive?.mode?.name ?: "null"),
                "runId" to "post-fix"
            )
        )
        // #endregion
        if (config.debugTelemetry) {
            osLifecycleReceiver = object : android.content.BroadcastReceiver() {
                override fun onReceive(context: Context?, intent: Intent?) {
                    AgentDebugLog.log(
                        "H6",
                        "AssistService.osLifecycleReceiver",
                        "os_event",
                        mapOf("action" to intent?.action)
                    )
                }
            }
            val filter = IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_OFF)
                addAction(Intent.ACTION_SCREEN_ON)
                addAction(PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED)
            }
            registerReceiver(osLifecycleReceiver, filter)
        }
        bindCamera()
        SessionFlags.setActive(this, true)
        Watchdog.schedule(this)
        mainHandler.removeCallbacks(cameraStallCheck)
        mainHandler.postDelayed(cameraStallCheck, CAMERA_STALL_CHECK_MS)
        Log.i("AkshravaDebug", "svc_started screen_keep_alive=$holdingScreenOn mode=${screenKeepAlive?.mode}")
        am.statusKey(
            if (holdingScreenOn && holdingCpuAwake) {
                "op_starting"
            } else if (holdingScreenOn) {
                "op_starting_no_cpu_keepalive"
            } else {
                "op_starting_no_screen_keepalive"
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
        // Detach from PoseTracker before dropping it: the engine holds no sensor registration of
        // its own, so clearing the sink is what stops it being fed.
        poseTracker?.onAccelerometerSample = null
        gestureDetectorEngine = null
        ambientLightMonitor?.stop(); ambientLightMonitor = null
        screenKeepAlive?.stop(); screenKeepAlive = null
        osLifecycleReceiver?.let {
            try {
                unregisterReceiver(it)
            } catch (ex: Exception) {
                Log.w("AkshravaDebug", "lifecycle receiver unregister failed", ex)
            }
            osLifecycleReceiver = null
        }
        cameraProvider?.unbindAll()
        previewDrain?.release(); previewDrain = null
        cameraLifecycleOwner?.destroy(); cameraLifecycleOwner = null
        client?.close(); client = null
        alertManager?.shutdown(); alertManager = null
        poseTracker?.stop(); poseTracker = null
        frameExecutor?.shutdownNow(); frameExecutor = null
        frameEncoder = null
        releaseCpuWakeLock()
        http?.connectionPool?.evictAll()
        http?.dispatcher?.executorService?.shutdown()
        http = null
        reflexEngine.release()
        reflexEngine = DisabledReflexEngine()
        resetFrameSlot()
        lookRequested.set(false)
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

    private fun releaseCpuWakeLock() {
        val held = wakeLock
        wakeLock = null
        if (held?.isHeld == true) {
            runCatching { held.release() }.onFailure {
                Log.w("AkshravaVision", "CPU wake-lock release failed", it)
            }
        }
    }
    
    private fun bindCamera() {
        val generation = bindGeneration.incrementAndGet()
        val sessionGeneration = assistanceGeneration.get()
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                if (stopping || generation != bindGeneration.get()) {
                    // Bind completed after stop: never attach an analyzer to a shutdown executor.
                    runCatching {
                        val provider = future.get()
                        provider.unbindAll()
                    }.onFailure { Log.w("AkshravaVision", "late camera bind cleanup failed", it) }
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
                // Some OEMs deliver black / zero ImageAnalysis frames unless a
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
                analysis.setAnalyzer(exec) { image -> analyzeImage(image, sessionGeneration) }
                provider.unbindAll()
                provider.bindToLifecycle(owner, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
                // Start the stall clock at the moment frames become expected.
                //
                // This used to stay 0 until the FIRST frame arrived, and shouldRebindForStall
                // ignores 0. A bind that succeeds but never delivers -- an OEM HAL that accepts
                // the configuration and then emits nothing, which is exactly the failure this
                // detector exists for -- therefore armed nothing at all: the camera looked bound,
                // the socket stayed open, no drop or stall ever fired, and assistance was simply
                // over with no recovery path but a manual Stop/Start the user cannot know to
                // perform. Baselining here means silence from bind onwards counts, so the first
                // frame never arriving is treated exactly like frames stopping later.
                lastAnalyzeAtMs = SystemClock.elapsedRealtime()
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

    private fun analyzeImage(image: ImageProxy, sessionGeneration: Int) {
        var closed = false
        try {
            if (!isCurrentAssistance(sessionGeneration)) return
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
            if (!isCurrentAssistance(sessionGeneration)) return
            // #region agent log
            if (framesAnalyzed == 1L || framesAnalyzed % 90L == 0L) {
                    AgentDebugLog.log(
                        "H1",
                        "AssistService.analyzeImage:heartbeat",
                        "pipeline_alive",
                        mapOf(
                            "n" to framesAnalyzed,
                            "holdingScreenOn" to (screenKeepAlive?.isHoldingScreenOn() == true),
                            "keepAliveMode" to (screenKeepAlive?.mode?.name ?: "null"),
                            "canStream" to (client?.canStream() == true),
                            "framePending" to framePending.get(),
                            "sessionActive" to SessionFlags.isActive(this),
                            "sessionStale" to SessionFlags.isStale(this)
                        )
                    )
            }
            // #endregion

            if (batteryCritical || captureSuspendedForBattery || captureSuspendedForFailure) {
                // #region agent log
                if (framesAnalyzed <= 5L || framesAnalyzed % 30L == 0L) {
                    Log.i("AkshravaDebug", "frame_drop suspended battCrit=$batteryCritical suspBatt=$captureSuspendedForBattery suspFail=$captureSuspendedForFailure n=$framesAnalyzed")
                    AgentDebugLog.log(
                        "H5",
                        "AssistService.analyzeImage:suspend",
                        "frame_drop_suspended",
                        mapOf(
                            "n" to framesAnalyzed,
                            "battCrit" to batteryCritical,
                            "suspBatt" to captureSuspendedForBattery,
                            "suspFail" to captureSuspendedForFailure
                        )
                    )
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
                    AgentDebugLog.log(
                        "H2",
                        "AssistService.analyzeImage:canStream",
                        "frame_drop_canStream_false",
                        mapOf("n" to framesAnalyzed, "clientNull" to (currentClient == null))
                    )
                }
                // #endregion
                return
            }

            // One encode/upload at a time. CameraX KEEP_ONLY_LATEST already sheds older
            // buffers; this flag also stops us from racing the WebSocket in-flight slot.
            if (!tryClaimFrameSlot(sessionGeneration, now)) {
                val heldForMs = frameSlotAgeMs(sessionGeneration, now)
                // A slot held past the send-side settle timeout means nothing is going to release
                // it: the session is already dead from the user's point of view even though the
                // camera and socket both still look healthy. Log it loudly and distinctly rather
                // than as one more line in the routine shed stream above.
                if (heldForMs > FRAME_SLOT_WEDGED_MS) {
                    Log.w("AkshravaDebug", "frame_slot_wedged held_ms=$heldForMs n=$framesAnalyzed — recovering")
                    AgentDebugLog.log(
                        "H4",
                        "AssistService.analyzeImage:framePending",
                        "frame_slot_wedged",
                        mapOf("n" to framesAnalyzed, "heldForMs" to heldForMs)
                    )
                    // Release the wedged slot so frames can resume. Without this, framePending
                    // stays true forever and no frame is ever sent again — the session is
                    // silently dead while the camera and socket both still look healthy.
                    releaseFrameSlot(sessionGeneration)
                    // ProtocolClient owns a second exact-frame slot. Resetting only this service
                    // flag would make every later encode bounce off that still-held slot. Cancel
                    // the socket so its normal explicit outage/reconnect path rebuilds both.
                    if (isCurrentAssistance(sessionGeneration)) currentClient.recoverFrameStall()
                }
                // #region agent log
                else if (framesAnalyzed <= 5L || framesAnalyzed % 60L == 0L) {
                    Log.i("AkshravaDebug", "frame_drop framePending stuck n=$framesAnalyzed held_ms=$heldForMs")
                    AgentDebugLog.log(
                        "H4",
                        "AssistService.analyzeImage:framePending",
                        "frame_drop_pending_stuck",
                        mapOf("n" to framesAnalyzed, "heldForMs" to heldForMs)
                    )
                }
                // #endregion
                return
            }
            val priority = lookRequested.getAndSet(false)
            // Headset long-press look or a fresh turn asks for one immediate frame.
            val turning = poseTracker?.consumeTurn() ?: false
            maybeAnnounceTilt(now)
            if (!priority && !turning && now - lastCaptureMs < captureIntervalMs()) {
                releaseFrameSlot(sessionGeneration)
                return
            }

            val thumbnail = FrameGate.luma(image)
            val avgLuma = FrameGate.meanLuma(thumbnail)
            val isOccluded = thumbnail.isEmpty() || FrameGate.isOccluded(avgLuma)
            if (isOccluded) {
                consecutiveOccludedFrames++
            } else {
                consecutiveOccludedFrames = 0
            }

            // #region agent log
            if (framesAnalyzed <= 5L || framesAnalyzed % 30L == 0L || isOccluded) {
                Log.i("AkshravaDebug", "frame_luma n=$framesAnalyzed avgLuma=$avgLuma occluded=$isOccluded")
                AgentDebugLog.log(
                    "H1",
                    "AssistService.analyzeImage:luma",
                    if (isOccluded) "frame_near_black" else "frame_luma_ok",
                    mapOf(
                        "n" to framesAnalyzed,
                        "avgLuma" to avgLuma,
                        "occluded" to isOccluded,
                        "consecutiveOccludedFrames" to consecutiveOccludedFrames,
                        "holdingIntervalMs" to (now - lastCaptureMs)
                    )
                )
            }
            // #endregion
            if (isOccluded) {
                releaseFrameSlot(sessionGeneration)
                // lastCaptureMs gates the capture interval above (`now - lastCaptureMs <
                // captureIntervalMs()`), and every OTHER exit path in this function that reaches
                // this point updates it. This branch did not, so once the scene went dark
                // lastCaptureMs froze and the interval gate could never engage again: every
                // single raw sensor callback (~30 fps on this hardware) fell through to
                // FrameGate.luma() and re-ran this whole branch, indefinitely, for as long as the
                // camera stayed dark. A covered lens or a phone carried lens-down in a pocket —
                // an ordinary way for this user population to carry a phone — turned into an
                // unthrottled busy loop burning CPU/battery/heat on the exact hardware that can
                // least afford it, which is a very plausible route to "detection stops working
                // after a few minutes" that would never show up on a bench test with the phone
                // sitting lens-up on a desk.
                lastCaptureMs = now
                // Dropping the frame is immediate, but the spoken prompt waits for several
                // consecutive dark frames: one dark frame is ordinary (auto-exposure settling, a
                // passing shadow, the first buffer after a bind) and telling the user to uncover a
                // lens that is not covered teaches them to ignore the prompt that matters.
                if (isCurrentAssistance(sessionGeneration) &&
                    consecutiveOccludedFrames >= OCCLUDED_FRAMES_BEFORE_ANNOUNCE &&
                    now - lastDarkAnnounceMs > 8_000L
                ) {
                    lastDarkAnnounceMs = now
                    updateNotification("Camera is dark — uncover rear lens")
                    alertManager?.statusKey("op_camera_dark")
                    // #region agent log
                    AgentDebugLog.log(
                        "H1",
                        "AssistService.analyzeImage:darkAnnounce",
                        "dark_announced",
                        mapOf("n" to framesAnalyzed, "avgLuma" to avgLuma)
                    )
                    // #endregion
                }
                // Never upload black OEM buffers — YOLO returns empty and burns RTT budget.
                return
            }
            val isGlared = FrameGate.isGlared(thumbnail, avgLuma)
            if (isGlared) {
                consecutiveGlaredFrames++
            } else {
                consecutiveGlaredFrames = 0
            }
            if (consecutiveGlaredFrames >= FrameGate.GLARE_FRAMES_BEFORE_ANNOUNCE) {
                // Glare, like blur, never drops a frame: a false washout verdict would stop
                // assistance in bright outdoor conditions. Persistent evidence only produces a
                // bounded status prompt.
                if (isCurrentAssistance(sessionGeneration) && now - lastGlareAnnounceMs > 8_000L) {
                    lastGlareAnnounceMs = now
                    updateNotification("Camera blinded by light")
                    alertManager?.statusKey("op_camera_glare")
                    AgentDebugLog.log(
                        "H1",
                        "AssistService.analyzeImage:glareAnnounce",
                        "glare_announced",
                        mapOf("n" to framesAnalyzed, "avgLuma" to avgLuma)
                    )
                }
            }
            if (FrameGate.isBlurred(thumbnail)) {
                consecutiveBlurredFrames += 1
                // Blur and glare do not drop frames. Persistent evidence only produces a bounded
                // status prompt, because the cane/guide is primary when the camera cannot be trusted.
                if (isCurrentAssistance(sessionGeneration) &&
                    FrameGate.shouldAnnounceBlur(now, consecutiveBlurredFrames, lastCameraUnclearMs)
                ) {
                    lastCameraUnclearMs = now
                    // Name the fix first (F-72): a smeared lens on a pocket-carried donated phone
                    // is usually a fingerprint, and "unclear" alone gave the user nothing to do
                    // about it. The cane/guide fallback stays, because wiping may not help.
                    alertManager?.statusKey("op_camera_blurry")
                    updateNotification("Camera is blurry — wipe the lens")
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
                    releaseFrameSlot(sessionGeneration)
                    return
                }
            }
            // Blur and glare are recorded as cheap diagnostic signals by FrameGate, but never
            // used to drop a frame: a bad quality estimate must not become a missed-obstacle decision.
            previousThumbnail = thumbnail

            val encoder = frameEncoder
            if (encoder == null) {
                releaseFrameSlot(sessionGeneration)
                // Leave the close to `finally` — closing here as well double-closed the ImageProxy.
                return
            }
            val prepared = encoder.prepare(image, quality.maxSide)
            image.close()
            closed = true
            val frame = encoder.compressPrepared(prepared, quality.jpegQ)
            if (!isCurrentAssistance(sessionGeneration) || currentClient !== client) {
                releaseFrameSlot(sessionGeneration)
                return
            }
            // Fail-closed offline: without licensed TFLite weights, reflex never speaks hazards.
            if (reflexEngine.isArmed()) {
                reflexEngine.evaluate(frame)
            }

            lastCaptureMs = now
            consecutiveAnalyzeFailures = 0

            val poseSnapshot = poseTracker?.snapshot()
            if (!isCurrentAssistance(sessionGeneration) || currentClient !== client) {
                releaseFrameSlot(sessionGeneration)
                return
            }
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
                AgentDebugLog.log(
                    "H3",
                    "AssistService.analyzeImage:send",
                    if (sent) "frame_sent_ok" else "frame_send_failed",
                    mapOf("frameId" to frameId, "sent" to sent, "jpegBytes" to frame.jpeg.size)
                )
            }
            // #endregion
            if (!sent) releaseFrameSlot(sessionGeneration)
        } catch (ex: Exception) {
            // Log before recovering — silent failures in the analysis loop are dangerous
            // on a safety-critical system and produce no diagnostic output otherwise.
            Log.e("AkshravaVision", "analyzeImage error (frames=$framesAnalyzed)", ex)
            releaseFrameSlot(sessionGeneration)
            if (isCurrentAssistance(sessionGeneration)) {
                consecutiveAnalyzeFailures += 1
                val failNow = SystemClock.elapsedRealtime()
                if (consecutiveAnalyzeFailures >= ANALYZE_FAILURES_BEFORE_STOP) {
                    updateNotification("Camera processing failed")
                    stopAfterUnrecoverableFailure("op_camera_failed", "Camera processing failed")
                } else if (shouldAnnounceAnalyzeFailure(
                        failNow, consecutiveAnalyzeFailures, lastAnalyzeFailureAnnounceMs
                    )
                ) {
                    lastAnalyzeFailureAnnounceMs = failNow
                    updateNotification("Camera processing error")
                    alertManager?.statusKey("op_analyze_failed")
                }
            }
        } finally {
            if (!closed) image.close()
        }
    }

    private fun isCurrentAssistance(generation: Int): Boolean =
        !stopping && generation == assistanceGeneration.get()

    private fun tryClaimFrameSlot(generation: Int, nowMs: Long): Boolean =
        synchronized(frameSlotLock) {
            if (!isCurrentAssistance(generation) || !framePending.compareAndSet(false, true)) {
                false
            } else {
                framePendingSinceMs = nowMs
                true
            }
        }

    private fun frameSlotAgeMs(generation: Int, nowMs: Long): Long =
        synchronized(frameSlotLock) {
            if (!isCurrentAssistance(generation) || framePendingSinceMs <= 0L) {
                0L
            } else {
                nowMs - framePendingSinceMs
            }
        }

    private fun releaseFrameSlot(generation: Int) {
        synchronized(frameSlotLock) {
            if (isCurrentAssistance(generation)) {
                framePendingSinceMs = 0L
                framePending.set(false)
            }
        }
    }

    private fun resetFrameSlot() {
        synchronized(frameSlotLock) {
            framePendingSinceMs = 0L
            framePending.set(false)
        }
    }

    private fun onFrameSlotSettled(generation: Int) {
        releaseFrameSlot(generation)
        if (!isCurrentAssistance(generation)) return
        val deferred = deferredAnalysisSide ?: return
        if (client == null) return
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

    /**
     * Rebind the camera for a new analysis resolution, but not more often than
     * [MIN_QUALITY_REBIND_INTERVAL_MS].
     *
     * The server's quality ladder steps at 150 ms of inference and measured inference on the
     * live deployment is 129-324 ms, so it sits right on that boundary and the advised max_side
     * flips between rungs continuously. Every flip used to unbind and rebind CameraX, which
     * costs 1-2 s of frames each time — observed live as 640 -> 512 -> 640 inside three seconds.
     * That is a detection blackout the user experiences as intermittent failure, and repeated
     * rebinds are also how OEM camera HALs get wedged.
     *
     * Running one rung off the server's advice for a few seconds is far cheaper than a rebind,
     * so a too-soon change is simply skipped; quality hints arrive about once a second, so the
     * next one re-evaluates as soon as the cooldown has passed. Stall recovery deliberately does
     * NOT come through here — it calls bindCamera() directly and must never be delayed.
     */
    private fun scheduleCameraRebind(target: Int) {
        val now = SystemClock.elapsedRealtime()
        if (now - lastQualityRebindAtMs < MIN_QUALITY_REBIND_INTERVAL_MS) {
            Log.i("AkshravaDebug", "camera_rebind_suppressed target=$target bound=$boundAnalysisMaxSide")
            deferredAnalysisSide = null
            return
        }
        lastQualityRebindAtMs = now
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
        if (client?.isTerminal() == true) return
        if (now - lastHeartbeatMs < HEARTBEAT_INTERVAL_MS) return
        lastHeartbeatMs = now
        SessionFlags.heartbeat(this)
        maybeRenewWakeLocks(now)
    }

    /**
     * Re-arm the timed CPU and screen wake locks while frames are still flowing.
     *
     * Driven from the heartbeat rather than a timer on purpose: the heartbeat only fires from the
     * camera analysis callback, so a session that has genuinely stopped producing frames stops
     * renewing and its locks lapse on schedule.
     */
    private fun maybeRenewWakeLocks(now: Long) {
        if (now - lastWakeLockRenewAtMs < WAKE_LOCK_RENEW_INTERVAL_MS) return
        lastWakeLockRenewAtMs = now
        val cpuRenewed = runCatching {
            wakeLock?.acquire(WAKE_LOCK_TIMEOUT_MS)
            wakeLock?.isHeld == true
        }.onFailure {
            Log.e("AkshravaVision", "CPU wake-lock renewal failed", it)
        }.getOrDefault(false)
        val screenRenewed = screenKeepAlive?.renew() == true
        if ((!cpuRenewed || !screenRenewed) && !wakeKeepAliveWarningAnnounced) {
            wakeKeepAliveWarningAnnounced = true
            Log.e(
                "AkshravaVision",
                "wake keep-alive lost cpu=$cpuRenewed screen=$screenRenewed mode=${screenKeepAlive?.mode}"
            )
            alertManager?.statusKey("op_power_keepalive_lost")
        }
        Log.i(
            "AkshravaDebug",
            "wake_locks_renewed cpu=$cpuRenewed screen=$screenRenewed mode=${screenKeepAlive?.mode}"
        )
    }

    private fun maybeAnnounceTilt(now: Long) {
        val pitch = poseTracker?.snapshot()?.pitchCdeg
        extremeTiltSinceMs = PoseTracker.extremeSinceUpdated(now, pitch, extremeTiltSinceMs)
        if (!PoseTracker.shouldAnnounceTilt(now, extremeTiltSinceMs, lastTiltAnnounceMs)) return
        lastTiltAnnounceMs = now
        alertManager?.statusKey("op_phone_tilted", haptic = true)
        updateNotification("Phone tilted — point camera forward")
        Log.i("AkshravaDebug", "tilt_announced pitch_cdeg=$pitch")
    }

    /**
     * Speak one ambient light edge (F-71), or drop it.
     *
     * Dropped rather than deferred on purpose. This is the lowest tier of speech in the app: it
     * describes the environment and makes no claim about what is ahead, so it must never cut off
     * a hazard alert that is still landing. By the time a deferral would have expired the edge is
     * old news, and the monitor has already adopted the new level, so nothing repeats later.
     *
     * No notification update either — the light changing is not a fault state to display.
     */
    private fun announceAmbientLightEdge(am: AlertManager, level: AmbientLightLevel) {
        val now = SystemClock.elapsedRealtime()
        if (am.hazardSpokenWithin(now)) {
            Log.i("AkshravaDebug", "ambient_light_edge_skipped level=$level reason=alert_busy")
            return
        }
        val textKey = when (level) {
            AmbientLightLevel.DARK -> "op_env_dark"
            AmbientLightLevel.BRIGHT -> "op_env_bright"
        }
        am.statusKey(textKey)
        Log.i("AkshravaDebug", "ambient_light_edge level=$level")
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
            alertManager?.statusKey("op_thermal_slow")
        } else if (temperature in 0f..THERMAL_CLEAR_C && thermalThrottled) {
            thermalThrottled = false
            capturePolicy.thermalThrottled = false
        }
        
        // Check battery level. Reuses the sticky intent already fetched above rather than a
        // second binder call.
        val batteryPct = DeviceCapability.batteryPercent(batteryStatus)
        if (batteryPct != null) {
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
                        // The F-15 gauge rides on the warning that already exists: the moment the
                        // user needs the number is the moment they have to decide whether to keep
                        // walking, and it costs no extra utterance.
                        val prefix = alertManager?.operationalText("op_battery_low").orEmpty()
                        if (prefix.isNotEmpty()) {
                            // Percent is language-neutral; batteryStatusText() is English hours.
                            alertManager?.status("$prefix $batteryPct%")
                        }
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
        val gen = assistanceGeneration.get()
        alertManager?.statusKey("op_battery_critical") {
            ContextCompat.getMainExecutor(this).execute {
                if (assistanceGeneration.get() == gen) stopAssistance()
            }
        }
    }

    private fun stopAfterCameraFailure() {
        stopAfterUnrecoverableFailure("op_camera_failed", "Rear camera unavailable")
    }

    private fun stopAfterUnrecoverableFailure(speechKey: String, notificationText: String) {
        if (captureSuspendedForFailure) return
        // A failed bind used to leave the WebSocket, sensors, wake lock and foreground service
        // running indefinitely even though the phone could no longer see. Stop capture now and
        // release the final TTS resource only after the accessibility warning completes.
        captureSuspendedForFailure = true
        SessionFlags.setActive(this, false)
        Watchdog.cancel(this)
        updateNotification(notificationText)
        cameraProvider?.unbindAll()
        previewDrain?.release(); previewDrain = null
        cameraLifecycleOwner?.destroy(); cameraLifecycleOwner = null
        client?.close(); client = null
        poseTracker?.stop(); poseTracker = null
        // Stop here, not just in teardown: the camera is already gone, and an ambient-light line
        // arriving between now and the deferred stop would tell a user who cannot see the screen
        // that something is still watching.
        ambientLightMonitor?.stop(); ambientLightMonitor = null
        frameExecutor?.shutdownNow(); frameExecutor = null
        releaseCpuWakeLock()
        // Capture generation before scheduling deferred stops. A Start that bumps
        // assistanceGeneration makes both the TTS callback and the hard timeout no-ops.
        val gen = assistanceGeneration.get()
        alertManager?.speakThenKey(speechKey) {
            ContextCompat.getMainExecutor(this).execute {
                if (assistanceGeneration.get() == gen) stopAssistance()
            }
        }
        // Hard timeout so a stuck TTS callback cannot leave the FGS running forever.
        stopHardTimeoutGeneration = gen
        mainHandler.removeCallbacks(stopHardTimeout)
        mainHandler.postDelayed(stopHardTimeout, STOP_HARD_TIMEOUT_MS)
    }

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL_ID, getString(R.string.notification_channel_name), NotificationManager.IMPORTANCE_LOW)
        channel.description = "Visible while camera assistance is active"
        notificationManager.createNotificationChannel(channel)
    }

    // The stop action and the manager handle never change for the life of the service, but they
    // were re-derived on every notification rebuild — and rebuilds happen on every server result
    // (~1.2/s for the whole walk). PendingIntent.getBroadcast is a round trip to system_server,
    // so this was two binder calls per frame on exactly the donated low-end phones that can
    // least afford them, on top of camera, JPEG encode and upload.
    private val notificationManager by lazy { getSystemService(NotificationManager::class.java) }
    private val stopActionIntent by lazy {
        PendingIntent.getBroadcast(
            this, 0, Intent(this, StopReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    private fun notification(status: String = getString(R.string.notification_text)): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setColor(Color.BLUE)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(status)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(0, getString(R.string.action_stop), stopActionIntent)
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

    /**
     * Post a status to the ongoing notification, dropping no-op and burst updates.
     *
     * Successive results usually carry identical text ("Live · person"), so most calls are pure
     * cost. This surface is diagnostic — speech is the channel the user actually relies on — so
     * capping it at [MIN_NOTIFICATION_INTERVAL_MS] is safe. Suppressed updates are coalesced
     * rather than dropped: the newest text is always flushed once the window closes, so the
     * notification never ends up stuck showing a stale state.
     */
    private fun updateNotification(status: String) {
        if (client == null || stopping) return
        if (status == lastNotificationText) return
        queuedNotificationText = status
        val sinceLast = SystemClock.elapsedRealtime() - lastNotificationAtMs
        mainHandler.removeCallbacks(notificationFlush)
        if (sinceLast >= MIN_NOTIFICATION_INTERVAL_MS) {
            flushNotification()
        } else {
            mainHandler.postDelayed(notificationFlush, MIN_NOTIFICATION_INTERVAL_MS - sinceLast)
        }
    }

    private fun flushNotification() {
        val text = queuedNotificationText ?: return
        queuedNotificationText = null
        if (client == null || stopping) return
        lastNotificationText = text
        lastNotificationAtMs = SystemClock.elapsedRealtime()
        notificationManager.notify(NOTIFICATION_ID, notification(text))
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

    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        AgentDebugLog.log("H7", "AssistService.onTrimMemory", "memory_pressure", mapOf("level" to level))
        Log.w("AkshravaDebug", "onTrimMemory level=$level")
    }

    override fun onLowMemory() {
        super.onLowMemory()
        AgentDebugLog.log("H7", "AssistService.onLowMemory", "memory_pressure", mapOf("state" to "low"))
        Log.w("AkshravaDebug", "onLowMemory")
    }

    override fun onDestroy() { 
        AgentDebugLog.log("H7", "AssistService.onDestroy", "service_destroyed", mapOf("stopping" to stopping))
        Log.w("AkshravaDebug", "AssistService onDestroy stopping=$stopping")
        stopAssistance()
        super.onDestroy() 
    }
}
