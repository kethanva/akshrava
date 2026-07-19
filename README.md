# Akshrava

Akshrava is a safety-bounded Android + Python implementation of a recycled-phone assistive-vision pilot for blind and low-vision users in India.

It is **not** a navigation system, collision-avoidance system, or replacement for a cane, guide dog, or sighted guide. It never says a road is safe to cross. 

**Kinematic Honesty Boundary**: At low sample rates (1–3 FPS), bounding box growth confounds vehicle motion with wearer motion. The vehicle vocabulary is strictly limited to `vehicle nearby`. The system does not, and will not, infer "approaching" vehicles or closing speeds.

## Current implementation boundary

This repository is a **bench and supervised-pilot implementation**, not a complete field-ready
v1.4 system. The working transport, freshness, alert-policy and operations scaffolding is here;
detector validation, device calibration and controlled-course evidence are release gates.

- **Native Android app (`android/`)**:
  - CameraX capture with one frame in flight, thumbnail duplicate gating, an explicit
    camera-obscured prompt, adaptive quality, pose metadata, battery/thermal safeguards and
    visible foreground-service lifecycle.
  - TTS/haptic alert rate limiting and clear degraded-mode wording when the server or its
    detector is unavailable.
  - No local TFLite/reflex path is bundled. A future fallback needs its own model contract,
    target-device benchmark and controlled-course evidence before it can be considered.

- **Backend (`backend/`)**:
  - Authenticated JSON-header + JPEG WebSocket protocol, frame-size validation, a per-session
    token bucket, strict result expiry, and adaptive quality advice.
  - Persistence-only two-stage association and conservative alerts: no crossing advice,
    no approach/closing-speed claim, no numeric distance, and no single-frame urgent alert.
    Per-device geometry fails closed: only a provisioned, verified calibration profile plus
    fresh pose and agreeing range estimates can set `range_valid=true`; no numeric distance is
    spoken.
  - Aggregate, non-identifying Prometheus metrics for processed/rejected frames, alerts, and
    inference duration at `/metrics`.

- **Operations & Compliance (`docs/`, `infra/`)**:
  - Zero raw-image retention in the running backend and an explicit consent/privacy process.
  - **Device Provisioning**: `PROVISIONING_CHECKLIST.md` outlines the 6-step qualification flow to classify recycled phones into A/B/C deployment tiers.
  - **Supervised Trial Protocol**: `TRIAL_PROTOCOL.md` defines strict safety requirements,
    including a named mobility instructor with stop authority.

*(Note: Ultralytics YOLO weights are licensed under AGPL-3.0. For non-open-source deployments, enterprise licensing or alternative Apache-2.0 models are required).*

## Repository layout

| Path | Purpose |
|---|---|
| `android/` | Lean Android 8+ (API 26) Kotlin application; validate each donated phone before issue |
| `backend/` | FastAPI service, conservative IoU association, and geometry-gated hazard scorer |
| `infra/` | PostgreSQL, API and optional Caddy/TLS deployment |
| `docs/` | Wire protocol, privacy policies, provisioning, and trial protocols |
| `scripts/` | Backend test/run and token provisioning helpers |
| `RECYCLED_PHONE_ASSISTIVE_VISION_BUILD_PLAN.md` | The guiding v1.4 product and safety plan |
| `NOT_NOW.md` | Deferred device-agnostic features (GPS memory, looming detector, foveated upload) |

## Run locally

```bash
./scripts/test_backend.sh
./scripts/run_backend_dev.sh
```

Then visit `http://127.0.0.1:8000/healthz`. The development WebSocket accepts only `dev-device-token`; production requires a signed JWT. Note: `python3` must be used if `python` is unavailable in your environment.

## Build the Android app

Install Android SDK Platform 36 and Build Tools 36, then set `ANDROID_HOME` (or create an untracked `android/local.properties` with `sdk.dir=/path/to/sdk`).

```bash
cd android
./gradlew :app:assembleDebug
```

The application only accepts `wss://` endpoints in release builds. Debug builds accept `ws://` for a local emulator/device test. A visible user action starts the foreground camera service; it cannot, and must not, silently start from boot/background.

## Tests

```bash
./scripts/test_backend.sh
cd android && ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug
```

The Android module is configured and includes the Gradle wrapper. The first local Android build
may prompt for Android SDK Platform 36 and Build Tools 36 licences; CI installs the same components
before it runs the Android unit tests and builds the APK.

## Important implementation boundary

No real detector, local fallback model, production TLS domain, JWT secret, device calibration, or
field-validation evidence can responsibly be invented in source code. The supplied code makes all
of those integrations explicit and fails closed where possible. Follow [the operator runbook](docs/OPERATIONS.md) before moving beyond a bench test.

The required code, device, model and field sign-offs are collected in the [release gate](docs/RELEASE_GATE.md). Do not treat a green CI build as a field-use approval.
