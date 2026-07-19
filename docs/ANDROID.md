# Android compatibility and device policy

The universal APK has `minSdk=26` and `targetSdk=36`. Android 8/8.1 remains build-compatible;
Tier-A field devices are Android 10+, 64-bit ARM, and at least 4 GB RAM. Android version alone
does not qualify a donated phone.

## Compatibility and release matrix

The release workflow validates API 28–36 (Android 9 through 16, including 12L). It installs and
launches an instrumentation smoke test on each API; API 30+ covers typed camera foreground-service
compatibility, API 31 covers visible-activity-only starts, API 33 covers notification permission,
and API 34 covers the typed camera FGS requirement. The tag release repeats this matrix and refuses
to publish an APK without its configured signing key.

Required GitHub release secrets are `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`,
`ANDROID_KEY_ALIAS`, and `ANDROID_KEY_PASSWORD`. Local `assembleRelease` output is unsigned build
evidence only and must never be distributed.

## Resource and safety policy

The active service is bounded to:

- one 640×480 CameraX analysis stream and one analyzer thread;
- one JPEG frame in flight, no local recorder or image backlog;
- capture cadence decided by `CapturePolicy`: normally 1 FPS walking, 0.2 FPS stationary, no
  more than 2 FPS during a short re-check, and lower rates for thermal/battery protection;
- 25 Hz pose sensors only while assistance is active; and
- no bundled TFLite model, OCR, Bluetooth integration, or unreviewed offline fallback.

Android 8–9 use legacy foreground-service behavior; Android 10+ declares the camera type; Android
13+ requests notification permission. On critical battery, the app unbinds the camera, closes the
socket, and releases the wake lock. A watchdog may prompt a visible restart, but never starts a
camera service silently.

## Physical-device qualification

Emulator tests prove process startup and permission-safe onboarding. They do not prove camera
driver stability, OEM lock-screen behavior, headset behavior, carrier freshness, heat, battery, or
mount quality. Those are mandatory field gates in [FIELD_GUIDE.md](FIELD_GUIDE.md).
