# Project: Akshrava Test Coverage Improvement

## Architecture
- **Android Layer** (`android/app/src/main/java/org/akshrava/app/`):
  - Modules: CameraX capture, session manager, alerts engine, watchdog, WebSocket protocol client, TTS integration.
  - Test framework: JVM Unit Tests (`android/app/src/test/`), Mockito / JUnit4, Jacoco plugin (`android/app/build.gradle.kts`).
- **Backend Layer** (`backend/akshrava_backend/`):
  - Modules: FastAPI app main, service API, session_handler, detector (noop, ultralytics, remote), alert_policy, auth (RS256 PyJWT), storage (SQLAlchemy).
  - Test framework: Pytest suite (`backend/tests/`), pytest-cov plugin (`backend/pyproject.toml`), Redis mock, GCP remote worker mock, WebSockets mock.
- **iOS Layer** (`ios/Akshrava/` and `ios/AkshravaApp/`):
  - Modules: experimental Swift session/capture/audio/provisioning client and XcodeGen UIKit host.
  - Test framework: SwiftPM macOS tests plus an iOS simulator XCTest target in CI; unsigned and not a participant release.

## Code Layout
- Android Java/Kotlin source: `android/app/src/main/java/org/akshrava/app/`
- Android JVM Unit Tests: `android/app/src/test/java/org/akshrava/app/`
- Android Build Script: `android/app/build.gradle.kts`
- Backend Source: `backend/akshrava_backend/`
- Backend Tests: `backend/tests/`
- Backend Config: `backend/pyproject.toml`

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Android Test Coverage & Jacoco | JVM unit/integration tests for org.akshrava.app + Jacoco report task | none | ACTIVE — report configured; coverage threshold is not enforced by Gradle |
| 2 | Backend Test Coverage & Pytest-cov | FastAPI pytest suite with Redis/GCP/WS mocks + pytest-cov fail-under 80% | none | COMPLETE — full suite is the enforced backend gate |
| 3 | E2E Testing & Verification Track | Baseline runner, Android instrumentation, live-path scripts, and supervised device evidence | M1, M2 | PARTIAL — automation exists; physical-trial evidence remains a field gate |

## Interface Contracts
### Android App ↔ Backend WebSocket Protocol
- Frames: JSON text header followed immediately by a binary JPEG payload; the header carries metadata and capability flags.
- Responses: JSON alert events (detected classes, bounding box info, warning text).
- Crucial Constraint: STRICT SAFETY BOUNDARY — Object/vehicle awareness ONLY. Product code,
  user-facing strings, TTS, and positive fixtures must not add navigation, crossing, collision,
  approach-speed, clear-path, or "safe" claims. Policy tests and documentation may quote those
  prohibited concepts only to enforce or explain the boundary.

### Non-negotiable behaviors (tests must assert these, never the inverse)
- **Only a deliberate user mute silences speech.** A headset route change announces and keeps speaking. A test that asserts "unplug → muted" is asserting a defect.
- **Repeating alert tones are bounded.** The stale-inference tick keys off an outstanding in-flight frame (never wall-clock since the last result) and stops after 3 emissions per frame.
- **Frame-quality gates debounce the prompt, not the drop.** Occlusion is `luma < 8`; dim-but-usable scenes (8–20) must keep streaming.
- **The battery gauge is labelled an estimate** and never reports `0 hours` while the phone is running.
