# Project: Akshrava Test Coverage Improvement

## Architecture
- **Android Layer** (`android/app/src/main/java/org/akshrava/app/`):
  - Modules: CameraX capture, session manager, alerts engine, watchdog, WebSocket protocol client, TTS integration.
  - Test framework: JVM Unit Tests (`android/app/src/test/`), Mockito / JUnit4, Jacoco plugin (`android/app/build.gradle.kts`).
- **Backend Layer** (`backend/akshrava_backend/`):
  - Modules: FastAPI app main, service API, session_handler, detector (noop, ultralytics, remote), alert_policy, auth (RS256 PyJWT), storage (SQLAlchemy).
  - Test framework: Pytest suite (`backend/tests/`), pytest-cov plugin (`backend/pyproject.toml`), Redis mock, GCP remote worker mock, WebSockets mock.

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
| 1 | Android Test Coverage & Jacoco | JVM unit/integration tests for org.akshrava.app + Jacoco setup >=80% | none | IN_PROGRESS |
| 2 | Backend Test Coverage & Pytest-cov | FastAPI pytest suite with Redis/GCP/WS mocks + pytest-cov setup >=80% | none | IN_PROGRESS |
| 3 | E2E Testing & Verification Track | Comprehensive dual-track verification & simulated E2E test suite | M1, M2 | IN_PROGRESS |

## Interface Contracts
### Android App ↔ Backend WebSocket Protocol
- Frames: Binary JPEG frame payload + JSON header/metadata.
- Responses: JSON alert events (detected classes, bounding box info, warning text).
- Crucial Constraint: STRICT SAFETY BOUNDARY — Object/vehicle awareness ONLY. Zero navigation, crossing, collision, approach-speed, clear-path, or "safe" wording allowed anywhere in tests, mocks, code, or strings.

### Non-negotiable behaviors (tests must assert these, never the inverse)
- **Only a deliberate user mute silences speech.** A headset route change announces and keeps speaking. A test that asserts "unplug → muted" is asserting a defect.
- **Repeating alert tones are bounded.** The stale-inference tick keys off an outstanding in-flight frame (never wall-clock since the last result) and stops after 3 emissions per frame.
- **Frame-quality gates debounce the prompt, not the drop.** Occlusion is `luma < 8`; dim-but-usable scenes (8–20) must keep streaming.
- **The battery gauge is labelled an estimate** and never reports `0 hours` while the phone is running.
