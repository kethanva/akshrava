# Akshrava E2E Test Suite Infrastructure & Specification Design

**Status:** This is a target matrix and harness design, not a claim that every named test exists
today. The currently executable gates are `scripts/run_e2e_tests.sh`, the tests under
`backend/tests/` and `android/app/src/test/`, Android instrumentation under
`android/app/src/androidTest/`, and the iOS simulator target configured in
`ios/AkshravaApp/project.yml`. Keep proposed test IDs below aligned with those real paths before
using them as acceptance evidence.

## 1. Executive Summary & Test Philosophy

Akshrava is a safety-first **assistive vision system** designed for supervised use by blind and low-vision individuals running on recycled Android hardware communicating with a FastAPI cloud backend. Because Akshrava operates in real-world physical environments, software reliability, bounded latency, and predictable alert behavior are critical.

### 1.1 Test Philosophy
The E2E test infrastructure enforces an **Opaque-Box (Black-Box) Testing Methodology**. The primary goal is to simulate realistic, multi-minute end-to-end user walking sessions, network degradations, hardware stress states, and backend failovers without relying on internal implementation hooks or white-box state manipulation during test execution.

Key principles:
1. **Opaque-Box Verification**: Tests interact strictly via public interface contracts—WebSocket frames, REST endpoints, OkHttp protocol handlers, Android CameraX frame input abstractions, and TTS output events.
2. **Deterministic Hermetic Execution**: Tests run in isolation using lightweight, high-fidelity mock components without requiring live GCP infrastructure or physical mobile hardware.
3. **Strict Safety Boundary Isolation**: Tests verify object and vehicle awareness alerts only. No
   test fixture or positive assertion may emit navigation guidance, crossing advice, collision
   warnings, approach-speed calculations, clear-path assurances, or "safe" phrasing. Negative
   policy tests may quote those prohibited terms solely to prove that they are rejected.
4. **Reproducible Failure Diagnosis**: All E2E test runs output structured logs, metric traces, and deterministic frame timelines for instant root-cause analysis.

---

## 2. Opaque-Box E2E Testing Methodology

The system is evaluated as a closed loop consisting of two primary boundary endpoints:
- **Client Boundary**: Android `ProtocolClient`, `FrameGate`, `AlertManager`, and `Watchdog`.
- **Backend Boundary**: FastAPI `main.py` session websocket, PyJWT auth, `SessionAdmission`, `detector.py`, `hazards.py`, `alert_policy.py`, `composer.py`, and `storage.py`.

```
+-----------------------------------------------------------------------------------+
|                              OPAQUE-BOX E2E BOUNDARY                              |
|                                                                                   |
|  +--------------------+ JSON header + JPEG bytes +----------------------------+  |
|  |  Android Client    |-------------------------->| FastAPI Backend Endpoint   |  |
|  |  Mock / Harness    |<--------------------------| (/v1/session WebSocket)    |  |
|  +--------------------+      JSON results/errors  +----------------------------+  |
|            |                                                    |                 |
|     TTS Speech Output                                    Detector & Storage DB    |
|   & Audio Focus Events                                (Mocked Remote YOLO Worker) |
+-----------------------------------------------------------------------------------+
```

### 2.1 Interface & Protocol Contracts
- **WebSocket Session Handshake**:
  - Connection: `WS /v1/session` with an `Authorization: Bearer <RS256_JWT>` header
  - Server Handshake: `{"type": "ready", "device_id": "...", "max_in_flight": 1, "detector": "...", "vision_enabled": true, "alert_max_age_ms": 2500, "capabilities": [...]}`
- **Frame Transmission Protocol**:
  - Client sends one JSON text header followed immediately by one binary JPEG payload. The
    header carries frame ID, dimensions, capture timestamps, calibration, quality/pose metadata,
    and capability flags; there is no `AKSH0001` binary envelope.
  - Rate limits: 1.2 FPS normal, 0.5 FPS priority look requests (with burst allowance up to 2.0 FPS).
- **Backend Alert Payload Protocol**:
  - Server returns a compact result such as `{"type": "result", "frame_id": 1, "capture_mono_ms": 123, "hazard": null, "detection_count": 0, "late_suppressed": false}`; the phone resolves any admitted message key through its local templates.
  - Error frames: `{"type": "error", "code": "worker_saturated"}` (transient, socket preserved) or disconnect codes (`4401` unauthorized, `4403` revoked).

---

## 3. Core Feature Inventory & Target Test Matrix

The Akshrava system architecture comprises 8 core features. The table below details source requirements, core components, and target test counts across the 4 testing tiers.

| Feature ID | Feature Name | Core Code Components | Source Requirements | Tier 1 (Unit Feature) | Tier 2 (Boundaries) | Tier 3 (Pairwise) | Tier 4 (Scenarios) | Target Total |
|---|---|---|---|---|---|---|---|---|
| **F1** | CameraX Capture & Pre-encode Frame Gating | `FrameGate.kt`, `FrameEncoder.kt`, `CapturePolicy.kt` | Luma occluded (<8, **drop is immediate**; the spoken prompt debounces ≥3 frames), glare (>230), burst MAD (<6), Laplacian variance monitoring, JPEG pre-encode | >= 5 | >= 5 | Integrated | Integrated | 10+ |
| **F2** | Session Management & Endpoint Policy | `AssistService.kt`, `EndpointPolicy.kt`, `session_admission.py`, `SessionFlags.kt` | Session admission cap, lease renewal, HTTPS/WSS URL validation, foreground lifecycle | >= 5 | >= 5 | Integrated | Integrated | 10+ |
| **F3** | Protocol Client Infrastructure & WS Transport | `ProtocolClient.kt`, `main.py` WS session, `FrameStreamHandler` | Handshake `ready`, JSON-header + binary JPEG framing, max in-flight (1), settle timeout (10s), reconnect backoff | >= 5 | >= 5 | Integrated | Integrated | 10+ |
| **F4** | Multi-Engine Detection Dispatch & Failover | `detector.py` (`Noop`, `Ultralytics`, `RemoteWorker`, `RegistryRemoteWorker`) | HMAC-SHA256 worker authentication, sticky multi-worker ordering, 503 worker saturation, fallback | >= 5 | >= 5 | Integrated | Integrated | 10+ |
| **F5** | Alert Policy, Range Estimation & Speech Composition | `hazards.py`, `alert_policy.py`, `composer.py`, `AlertManager.kt` | Distance estimation (pinhole/ground), S1 urgent (1 frame) vs S2 caution (2 hit persistence), 800ms debounce, speech templates | >= 5 | >= 5 | Integrated | Integrated | 10+ |
| **F6** | Watchdog & Platform Health Monitoring | `Watchdog.kt`, `service.py` circuit breaker, `ProtocolClient.shouldTickStaleInference`, camera rebind | Circuit breaker (3 fails -> 5s cooldown), stale-inference tick (outstanding frame >3s, **max 3 ticks**), 10s settle timeout, 15s camera rebind, 3min alarm | >= 5 | >= 5 | Integrated | Integrated | 10+ |
| **F7** | Storage, Device Authentication & Diagnostics | `storage.py`, `auth.py` (RS256), `gcp_storage.py` | PyJWT RS256 claims validation, revocation checks, DB alert event persistence, opt-in diagnostic uploads | >= 5 | >= 5 | Integrated | Integrated | 10+ |
| **F8** | Hardware Adaptation, Power & Audio Focus | `ScreenKeepAlive.kt`, `AlertAudioFocus.kt`, `DeviceCapability.batteryStatusText`, `HeadsetControls`, thermal/battery monitors | Thermal throttling (>=43°C), low battery warnings (<15% warn, <10% stop) carrying the F-15 gauge, refcounted audio-focus ducking, headset route loss announces (**never mutes**), TTS engine recovery | >= 5 | >= 5 | Integrated | Integrated | 10+ |

---

## 4. E2E Test Architecture & Directory Layout

The executable tests live in the existing unit and instrumentation directories. The matrix below
may refer to future target cases, but documentation must not imply that every named test or a
separate `e2e/` package already exists:

```
Akshrava Repo Root
├── backend/
│   └── tests/
│       ├── test_websocket.py              # Current protocol/session integration coverage
│       └── ... (existing backend unit tests)
├── android/
│   └── app/
│       └── src/
│           └── test/
│               └── java/
│                   └── org/
│                       └── akshrava/
│                           └── app/      # Current JVM unit/protocol tests
├── scripts/
│   ├── run_e2e_tests.sh                    # Backend + Android JVM baseline runner
│   └── e2e_device_soak.sh                  # Physical-device soak runner
└── TEST_INFRA.md                          # Main E2E Specification & Infra Document (this file)
```

The feature IDs and mock components below remain a target matrix. The authoritative current file
list is the filesystem under `backend/tests/`, `android/app/src/test/`, and
`android/app/src/androidTest/`; do not cite proposed test names as executed evidence.

---

## 5. 6-Component Mock Suite Design

To support isolated, repeatable E2E testing without external cloud or hardware dependencies, the E2E framework defines 6 mock components:

```
                   +-----------------------------------------------+
                   |          6-COMPONENT MOCK SUITE               |
                   +-----------------------------------------------+
                   | 1. Sockets & Transport Mock (AsyncTestClient)  |
                   | 2. Camera & Sensor Frame Generator Mock       |
                   | 3. Stateful FakeRedis Session & Rate Store    |
                   | 4. GCP Remote YOLO Worker Mock (HMAC/Respx)    |
                   | 5. Auth RS256 Keypair & Token Generator       |
                   | 6. FastAPI Test Harness & App Factory         |
                   +-----------------------------------------------+
```

### 5.1 Mock 1: Sockets & WebSocket Transport Mock
- **Purpose**: Simulates full binary JPEG frame transfer, JSON header parsing, WebSocket handshake, ready signals, and disconnect error handling.
- **Implementation**: Uses the backend's existing pytest/WebSocket fixtures and Android JVM test
  doubles; live device behavior is covered only by `androidTest` and the documented soak scripts.
- **Capabilities**:
  - Validates JSON-header followed by binary-JPEG framing and stream alignment.
  - Injects delayed frames, dropped connections, and malformed header payloads.
  - Simulates socket disconnect codes (`4401`, `4403`) and soft error frames (`worker_saturated`).

### 5.2 Mock 2: Camera & Sensor Frame Generator Mock
- **Purpose**: Generates valid JPEG binary image buffers and synchronized phone pose sensor data.
- **Capabilities**:
  - Generates synthetic 640x480 JPEG images with configurable luma averages (e.g. luma=4 for lens occlusion, luma=240 for glare) and Mean Absolute Difference (MAD) for motion burst detection.
  - Generates realistic phone orientation metadata (roll, pitch, yaw in centidegrees, e.g. roll=500 -> 5.0°, pitch=-1000 -> -10.0°).
  - Simulates sensor dropout (stale pose >100ms) to exercise range validity gating.

### 5.3 Mock 3: Stateful Redis Simulator (`FakeRedisStore`)
- **Purpose**: Simulates Redis session admission leases, rate limiting sliding windows, nonce replay protection, and device token revocation in memory.
- **Capabilities**:
  - Implements `try_open()`, `renew()`, and `close()` for session admission bounds.
  - Tracks client frame rate limits (1.2 FPS ambient vs 0.5 FPS priority).
  - Supports dynamic device revocation lists to test mid-session token invalidation.

### 5.4 Mock 4: GCP Remote YOLO Worker Mock
- **Purpose**: Intercepts HTTP requests sent by `RemoteWorkerDetector` to simulate private remote GPU worker responses.
- **Implementation**: Uses `httpx.MockTransport` / `respx` mocking framework.
- **Capabilities**:
  - Verifies HMAC SHA256 signature headers (`X-Akshrava-Timestamp`, `X-Akshrava-Nonce`, `X-Akshrava-Signature`).
  - Returns simulated YOLO detection bounding boxes (e.g. `car` at 3.2m, `person` at 1.8m, `bus` at 5.0m).
  - Simulates latency injection (e.g. 500ms delay) to trigger late frame suppression (`inference_ms > alert_max_age_ms`).
  - Simulates HTTP 503 Service Unavailable to trigger `WorkerSaturatedError` and circuit breaker trips.

### 5.5 Mock 5: Auth RS256 PyJWT Mock
- **Purpose**: Provides cryptographic RSA 2048 keypairs and token generation utilities for authenticating devices.
- **Capabilities**:
  - Generates valid signed RS256 PyJWT tokens containing required claims (`exp`, `sub`, `aud="akshrava-device"`, `diagnostic_consent`).
  - Generates expired tokens, tokens with invalid signatures, and tokens with wrong audiences (`aud="invalid"`).
  - Supports dual-key cutover testing using `jwt_public_key_file` and `jwt_public_key_previous_file`.

### 5.6 Mock 6: FastAPI Client Harness & App Factory
- **Purpose**: Manages clean FastAPI application lifecycle overrides per test case with `DEV_AUTH_BYPASS=false`.
- **Capabilities**:
  - Overrides FastAPI app dependencies (`app.dependency_overrides`).
  - Configures isolated in-memory SQLite database sessions using SQLAlchemy metadata.
  - Resets alert policy state, session admission counters, and circuit breaker metrics between tests.

---

## 6. 4-Tier Test Matrix Specifications

### 6.1 Tier 1: Core Feature E2E Coverage (>=5 Tests per Feature)

#### F1: CameraX Capture & Pre-encode Frame Gating
- `test_f1_01_valid_frame_pass`: Valid 640x480 frame with normal luma (120) and MAD (15) passes pre-encode gate.
- `test_f1_02_occluded_lens_drop`: Frame with low luma (<8) dropped by `FrameGate` as occluded lens on the **first** occluded frame; the `Camera is dark. Uncover the rear lens.` prompt only fires after 3 consecutive.
- `test_f1_02b_dim_scene_not_occluded`: Dim-but-usable scenes (luma 8–20: dusk, unlit corridor, shaded underpass) must **not** be treated as occluded — a false occlusion verdict stops assistance in exactly the light where the user has least other information.
- `test_f1_03_glare_drop`: Frame with high luma (>230) and low variance (<40) dropped as glare.
- `test_f1_04_duplicate_burst_drop`: Frame with low MAD (<6) compared to previous frame dropped as burst duplicate.
- `test_f1_05_motion_blur_metric`: Frame with low Laplacian variance (<12.0) recorded as diagnostic metric without dropping.

#### F2: Session Management & Endpoint Policy
- `test_f2_01_valid_session_admission`: Valid JWT token successfully admits new session when fleet cap is open.
- `test_f2_02_fleet_cap_rejection`: Session rejected when active sessions equal `max_active_sessions`.
- `test_f2_03_lease_renewal_quiet_session`: Session lease automatically renewed upon frame transmission.
- `test_f2_04_endpoint_wss_enforcement`: `EndpointPolicy` rejects non-loopback HTTP/WS endpoints in production mode.
- `test_f2_05_session_close_cleanup`: Socket disconnect triggers `session_admission.close()` and releases fleet slot.

#### F3: Protocol Client Infrastructure & WS Transport
- `test_f3_01_handshake_ready`: WebSocket connection receives initial JSON `ready` message with `max_in_flight=1`.
- `test_f3_02_json_header_then_jpeg`: Client sends a JSON header followed by its binary JPEG and the backend preserves pairing/alignment.
- `test_f3_03_single_in_flight_enforcement`: Second frame sent while previous frame is in-flight is dropped or queued per client policy.
- `test_f3_04_frame_settle_timeout`: Frame with no response within 10,000ms triggers client frame settle timeout log and reset.
- `test_f3_05_reconnect_exponential_backoff`: Network disconnection triggers client exponential reconnect backoff strategy.

#### F4: Multi-Engine Detection Dispatch & Failover
- `test_f4_01_noop_detector`: `NoopDetector` returns empty detections list `[]` instantly.
- `test_f4_02_remote_worker_hmac_auth`: `RemoteWorkerDetector` includes valid HMAC-SHA256 signature headers on worker HTTP POST.
- `test_f4_03_worker_saturation_503`: Remote worker returning 503 raises `WorkerSaturatedError` and sends soft error frame.
- `test_f4_04_sticky_worker_ordering`: `RegistryRemoteWorkerDetector` orders remote workers deterministically per device ID.
- `test_f4_05_detector_failover`: Primary worker failure triggers fallback worker query in registry detector.

#### F5: Alert Policy, Range Estimation & Speech Composition
- `test_f5_01_urgent_s1_single_frame`: High-risk vehicle detection (risk >= 1.3, valid range) triggers S1 urgent alert on frame 1.
- `test_f5_02_caution_s2_persistence`: Moderate-risk obstacle requires 2 persistence hits before issuing S2 caution alert.
- `test_f5_03_range_validity_pose_rejection`: Stale pose (>100ms) or excessive roll (>12°) invalidates range estimation.
- `test_f5_04_alert_debounce`: Same-key alert issued within 800ms debounce window is suppressed.
- `test_f5_05_multi_lang_speech_composition`: Speech composer generates correct language template output (en, hi, ta, kn, ml, te).

#### F6: Watchdog & Platform Health Monitoring
- `test_f6_01_circuit_breaker_tripping`: 3 consecutive inference failures trip circuit breaker for 5.0s cooldown.
- `test_f6_02_circuit_breaker_cooldown_recovery`: After 5.0s cooldown, circuit breaker allows trial request and recovers on success.
- `test_f6_03_late_frame_suppression`: Inference taking >`alert_max_age_ms` marks frame `late_suppressed=True` and skips hazard scoring.
- `test_f6_04_stale_inference_watchdog`: A frame outstanding >3s triggers the stale earcon tick, and the tick stops after 3 emissions. Keyed on an **in-flight frame**, never wall-clock since the last result — `CapturePolicy` legitimately runs 5s intervals on low battery (0.2 FPS) and 2s under thermal throttle, so a wall-clock rule beeps continuously on a healthy session.
- `test_f6_05_camera_stall_rebind`: Camera freeze >15s triggers CameraX unbind/rebind cycle.

#### F7: Storage, Device Authentication & Diagnostics
- `test_f7_01_rs256_jwt_token_validation`: Backend successfully validates valid RS256 signed device token.
- `test_f7_02_revoked_token_disconnect`: Token matching revoked device ID results in WebSocket disconnect code 4403.
- `test_f7_03_alert_event_db_persistence`: Issued alert events persisted in DB asynchronously via `BackgroundTaskTracker`.
- `test_f7_04_key_rotation_fallback`: Key rotation allows validation against `jwt_public_key_previous_file`.
- `test_f7_05_diagnostic_upload_consent`: Diagnostic uploads execute only when `diagnostic_consent=True` and opt-in enabled.

#### F8: Hardware Adaptation, Power & Audio Focus
- `test_f8_01_thermal_throttling_fps_reduction`: Device temperature >=43°C reduces capture FPS target.
- `test_f8_02_low_battery_warning`: Battery <15% triggers the low battery warning, carrying the F-15 gauge (`Estimated …`, never `0 hours` while the phone is still running).
- `test_f8_03_critical_battery_shutdown`: Battery <10% safely stops vision capture service.
- `test_f8_04_audio_focus_ducking`: Active TTS utterance requests audio focus with transient ducking; the hold is refcounted, so focus is abandoned only after the **last** utterance ends. A hold is taken even when the system denies focus, otherwise the denial's release decrements a hold belonging to a different, still-speaking utterance.
- `test_f8_05_tts_engine_rebuild`: Dead TTS engine on OEM force-stop automatically rebuilds engine.
- `test_f8_06_headset_route_loss_never_mutes`: `ACTION_AUDIO_BECOMING_NOISY` (earbuds die / cable pulled) announces `Headset disconnected. Alerts now play on the speaker.` and **keeps alerts flowing**. Muting here would leave a user who cannot see the screen unable to distinguish a route change from a dead app.

---

### 6.2 Tier 2: Boundary & Corner Cases (>=5 Tests per Feature)

- **F1 Boundaries**:
  - Centidegree boundary testing (roll = 1200 [12.0°] vs 1201 [12.01°]).
  - Zero-byte JPEG payload handling.
  - Corrupted EXIF / header metadata.
  - Image size 32x32 minimum resolution boundary.
  - Frame arrival timestamp jitter (out-of-order frame index).
- **F2 Boundaries**:
  - Token expiration exactly at boundary (`exp == now`).
  - Active session limit saturation (`max_active_sessions - 1` vs `max_active_sessions`).
  - Malformed URL protocol strings (`http://production-domain/v1/session`).
  - Session lease expiration during active socket stream.
  - Rapid connect/disconnect loop (10 connections in 1 second).
- **F3 Boundaries**:
  - Large JSON header payload exceeding 4KB buffer.
  - Partial binary frame payload truncated mid-header.
  - Disconnect during active frame transmission.
  - Frame settle timeout occurring at 9999ms vs 10001ms.
  - Soft error `worker_saturated` received 5 times consecutively.
- **F4 Boundaries**:
  - Remote worker HMAC signature timestamp skew (>300 seconds).
  - Remote worker returning invalid JSON payload.
  - Worker timeout at 2000ms boundary.
  - All remote workers in registry returning 503 simultaneously.
  - Zero bounding boxes detected (`[]`) vs 50 bounding boxes detected.
- **F5 Boundaries**:
  - Bounding box coordinates out of frame bounds (`xmin < 0` or `xmax > 640`).
  - Range estimation agreement exactly at ±50% threshold.
  - Global rate limit saturation (6 alerts issued within 60 seconds).
  - Simultaneous S1 urgent and S2 caution hazards in single frame (S1 preemption).
  - Speech text template truncation for unknown language code fallback to `en`.
- **F6 Boundaries**:
  - Circuit breaker exactly on 3rd failure vs 2nd failure.
  - Circuit breaker cooldown elapsed at 4.9s (open) vs 5.1s (half-open).
  - Late frame suppression boundary (`inference_ms == alert_max_age_ms` vs `+1ms`).
  - Camera stall timer reset on 14.9s vs 15.1s.
  - Watchdog 3-minute alarm firing during background sleep state.
  - Stale tick at frame age 2.9s (silent) vs 3.1s (tick), and tick 3 (fires) vs tick 4 (silent).
  - Healthy session at 0.2 FPS (5s capture interval, no frame outstanding) emits **zero** stale ticks.
- **F7 Boundaries**:
  - RS256 token signed with 1024-bit key (rejected as weak).
  - Database connection pool exhaustion during background alert recording.
  - Token with missing `sub` claim.
  - Key rotation cutover with expired previous key.
  - Diagnostic upload bucket quota exceeded.
- **F8 Boundaries**:
  - Thermal state transition at exactly 42.9°C vs 43.0°C.
  - Battery percentage transition at exactly 15% and 10%.
  - Battery gauge wording at 1% / 3% / 4% (`less than an hour` → `roughly one hour`) — never `0 hours`.
  - Audio focus loss `AUDIOFOCUS_LOSS_TRANSIENT` during S1 urgent alert utterance.
  - Audio focus **denied** by the system while an earlier utterance still holds it (release must not abandon early).
  - Headset unplug during an in-progress S1 utterance (route change announced, alert not suppressed).
  - TTS engine rebuild streak reaching maximum threshold (4 failures).
  - Screen keep-alive flag toggle during background service destruction.

---

### 6.3 Tier 3: Cross-Feature Pairwise Interaction Matrix

Tier 3 tests evaluate interactions across pair combinations of core features:

```
    F1  F2  F3  F4  F5  F6  F7  F8
F1   -   X   X   .   .   .   .   .
F2   .   -   X   .   .   .   X   .
F3   .   .   -   X   .   X   .   .
F4   .   .   .   -   X   X   .   .
F5   .   .   .   .   -   .   .   X
F6   .   .   .   .   .   -   .   X
F7   .   .   .   .   .   .   -   .
F8   .   .   .   .   .   .   .   -
```

- **P1 (F1 + F3: FrameGate -> WS Protocol)**: Lens occlusion detected by `FrameGate` prevents binary frame payload transmission over WS client.
- **P2 (F3 + F4: WS Protocol -> Multi-Engine Detector)**: WebSocket frame stream dispatches frames to `RemoteWorkerDetector` with HMAC headers and forwards detections back over socket.
- **P3 (F4 + F5: Detector -> Alert Policy & Speech)**: Remote YOLO worker detections feed into pinhole/ground range estimation, triggering S1 urgent speech alerts for close vehicles.
- **P4 (F5 + F8: Speech Composition -> Audio Focus & Thermal)**: Issued speech alerts trigger Android audio focus ducking while thermal throttling dynamically adjusts target FPS.
- **P5 (F2 + F7: Session Admission -> Token Revocation & DB)**: Device token revocation in DB store immediately forces session admission disconnect (4403).
- **P6 (F3 + F6: WS Transport -> Watchdog Circuit Breaker)**: Repeated network/inference timeouts trip the backend circuit breaker, issuing `worker_saturated` soft errors to WS client.

---

### 6.4 Tier 4: Real-World Application Scenarios

Tier 4 tests execute multi-step end-to-end user journeys:

#### Scenario 1: Complete 10-Minute Simulated User Walking Session
- **Workflow**:
  1. Client authenticates via RS256 JWT token over WSS `/v1/session`.
  2. Receives `"ready"` handshake frame.
  3. Transmits 1.2 FPS frame stream with synchronized pose data for 10 simulated minutes.
  4. Encounters obstacle (`chair` at 2.5m) -> receives persistence S2 caution alert.
  5. Encounters a nearby vehicle -> receives the policy-admitted awareness alert when freshness and calibration gates allow it.
  6. Speech engine renders localized TTS utterances with audio focus ducking.
  7. Client cleanly disconnects socket upon session completion.

#### Scenario 2: Sudden GPU Worker Outage & Circuit Breaker Recovery
- **Workflow**:
  1. Active streaming session connected to remote GPU worker.
  2. GPU worker suddenly fails (returns HTTP 503).
  3. Backend logs `WorkerSaturatedError`, sends soft error `"code": "worker_saturated"` without dropping WS connection.
  4. After 3 consecutive failures, circuit breaker trips to OPEN state (5s cooldown).
  5. Subsequent frames during cooldown instantly return soft error without HTTP request.
  6. GPU worker recovers; after 5s cooldown, trial request succeeds and circuit breaker closes.

#### Scenario 3: Priority Look Request on Low Battery
- **Workflow**:
  1. Device battery drops to 14% (triggering low battery warning alert).
  2. User triggers priority look request (double-shake gesture — the gesture requests a look and speaks nothing extra, so it cannot flush a live hazard alert; 3s trigger cooldown).
  3. Client sends a priority frame into its separate bounded admission bucket.
  4. Backend dispatches it through the same freshness and awareness policy and returns one bounded summary.

#### Scenario 4: Mid-Session Device Revocation & Key Cutover
- **Workflow**:
  1. Active session streaming frames successfully.
  2. Operator revokes device ID in backend database / Redis revocation list.
  3. Next frame stream tick checks revocation status, closes session admission, and terminates socket with code `4403`.
  4. Client attempts reconnect; rejected during token validation.

#### Scenario 5: Rapid Lens Occlusion & Sensor Dropout Re-Admit
- **Workflow**:
  1. Client camera covered (hand over lens, luma <8) -> `FrameGate` drops capture from the first occluded frame; prompt after 3 consecutive.
  2. Phone pose sensor drops out (stale pose >100ms) -> backend invalidates range estimation.
  3. Camera uncovered and pose restored -> system automatically resumes range estimation and hazard alerts.

---

## 7. Test Execution & Verification Protocol

The executable baseline entry point is `scripts/run_e2e_tests.sh`; it runs the production-source
safety scan, backend pytest suite, and Android JVM suite. It does not replace physical-device
instrumentation or the iOS simulator target.

### 7.1 Script Execution Architecture
```
                                scripts/run_e2e_tests.sh
                                           |
                    +----------------------+----------------------+
                    |                                             |
          Backend pytest suite                         Android JVM unit/protocol suite
       (PYTHON_BIN=python3.12 pytest)             (./gradlew :app:testDebugUnitTest)
                    |                                             |
         backend/tests/                                 android/app/src/test/
                    |                                             |
         All 4 Tiers (1, 2, 3, 4)                     ProtocolClient & AlertManager
```

### 7.2 Manual Verification Commands
- **Run Full E2E Test Suite**:
  ```bash
  ./scripts/run_e2e_tests.sh
  ```
- **Run Backend Suite Direct**:
  ```bash
  PYTHON_BIN=python3.12 ./scripts/test_backend.sh
  ```
- **Run Android JVM Unit & E2E Suite Direct**:
  ```bash
  cd android
  ./gradlew :app:testDebugUnitTest
  ```

---

## 8. Strict Safety Boundary Audit & Compliance Checklist

### 8.1 Mandatory Safety Boundary Principle
Akshrava is an **awareness system only**. It provides object and vehicle presence information to supplement human judgment and mobility aids (e.g. white canes, guide dogs).

> **SAFETY BOUNDARY MANDATE**: Akshrava product code, user-facing strings, TTS output, and
> positive test fixtures MUST NEVER claim, imply, or add navigation guidance, street crossing
> decisions, collision avoidance warnings, approach-speed calculations, clear-path assurances, or
> "safe" guarantees. Policy tests and documentation may quote these terms only to explain or
> enforce that they are prohibited.

### 8.2 Prohibited Terms & Concepts Audit Checklist

| Prohibited Category | Forbidden Words & Phrases (Case-Insensitive) | System Rule / Requirement | Compliance Status |
|---|---|---|---|
| **Navigation** | `navigate`, `turn left`, `turn right`, `walk forward`, `step left`, `path ahead`, `route` | Zero directional navigation commands allowed. Alert must state object presence and bearing only (e.g., "Person ahead"). | **COMPLIANT** |
| **Crossing Decisions** | `cross now`, `safe to cross`, `don't cross`, `cross street`, `traffic clear` | Zero crossing instructions allowed. | **COMPLIANT** |
| **Collision Avoidance** | `collision imminent`, `avoid collision`, `impact in`, `crash warning` | Zero collision avoidance or time-to-impact phrasing allowed. | **COMPLIANT** |
| **Approach Speed** | `approaching at`, `speed`, `km/h`, `mph`, `closing speed`, `time to arrival` | Zero speed or approach rate calculations allowed. | **COMPLIANT** |
| **Clear Path** | `clear path`, `path is clear`, `way is clear`, `all clear`, `safe path` | "No alert in this recent view. Continue using cane or guide" MUST be used instead of "clear". | **COMPLIANT** |
| **Safe Guarantees** | `safe`, `safety confirmed`, `guaranteed clear`, `secure walk` | These phrases MUST NOT appear in alerts, speech output, or positive fixtures; policy documentation may quote them as forbidden examples. | **COMPLIANT** |

### 8.3 Automated CI Safety Boundary Scanner
As part of `scripts/run_e2e_tests.sh`, an automated grep check audits the current backend and
Android production-source paths. It is a source guard, not a substitute for runtime tests or the
iOS audit:

```bash
grep -rnE -i \
  "navigate|turn left|turn right|walk forward|cross now|safe to cross|collision imminent|approaching at|closing speed|clear path|path is clear|way is clear|safe" \
  backend/akshrava_backend/ android/app/src/main/ \
  | grep -v "STRICT SAFETY BOUNDARY" || true
```

The production-source scan is expected to report zero matches in a passing run. It intentionally
does not scan this design document or policy-focused tests, which quote prohibited terms to prove
that those terms are rejected; a passing scan is evidence for source hygiene, not a field-safety
or runtime-perception claim.
