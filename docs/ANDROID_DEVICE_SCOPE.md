# Android device scope and resource policy

The app supports Android 8+ (API 26), covering the intended donated-phone cohort from roughly
2018–2021. A current `compileSdk`/`targetSdk` does not change the Android versions on which the
APK can run.

The active service is intentionally bounded:

- one CameraX 640×480 analysis stream and one analyzer thread;
- one JPEG frame in flight, with no local video recorder or frame queue;
- default 1 FPS walking, 0.2 FPS stationary, and no more than 2 FPS during a short re-check;
- 25 Hz pose sensors only while assistance is active;
- no bundled TFLite model, OCR, MediaSession, Bluetooth integration, or local fallback model;
- camera unbound, socket closed and wake lock released when battery is critical.

Android 8–9 use the legacy foreground-service call; Android 10+ declares the camera service type,
and Android 13+ requests notification permission at runtime. Every phone still needs provisioning:
OEM camera behavior, lock-screen survival, heat, battery and carrier freshness vary more than the
Android version alone.
