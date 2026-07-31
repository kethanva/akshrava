# AGENT.md — End-to-End Testing & Debugging Playbook

How to prove (not assume) that the Android app stays connected to the live backend and keeps
producing detections, and how to tell a real fault apart from expected behavior when something
looks broken. This is the procedure actually used and verified on 2026-07-22 against a physical
OnePlus7T (Android 12) and the live GCP deployment (`detector: remote`): **11 min 08.9 s
continuous connection, single socket, zero reconnects, 650/650 frames answered.**

Read this before concluding "it's broken" — most apparent failures below are documented, expected
behavior, not bugs.

## 1. What "working end to end" actually means

The system has independent layers, and a symptom at the user's ear ("it stopped talking") can
originate in any of them. Before touching code, place the failure in one of these:

| Layer | Healthy signal | Broken signal |
|---|---|---|
| Backend reachability | `curl https://<host>/livez` → `{"ok":true}` | timeout / non-200 |
| WebSocket handshake | `ws_ready detector=... vision_enabled=true` once | `Provisioning required`, close code 4401/4403 |
| Frame transport | `frame_sent id=N` with N advancing, matching `AkshravaVision: frame=N` results | ids stall, `frame_drop framePending stuck` growing |
| Detection | `labels=[...]` non-empty on frames pointed at real objects | empty labels — may just mean nothing recognizable is in frame |
| Hazard speech | `announce_delivered` in logs, spoken alert heard | detected but silent — **check §3 before assuming a bug** |

## 2. Prerequisites

- A connected device: `adb devices` shows `device` (not `unauthorized`/`offline`).
- Backend live: `curl -s https://<host>/livez` and `/readyz` both `200`.
- Device provisioned (endpoint + encrypted token + calibration id on disk). One command does
  this from scratch:

  ```bash
  GOOGLE_APPLICATION_CREDENTIALS=<service-account.json> \
    ./scripts/install_android_debug_full.sh [device_serial]
  ```

  This mints a fresh device JWT, builds the debug + test APKs, installs both, grants runtime
  permissions it can, provisions the Keystore, and — critically — **proves the live path itself**
  before declaring success (opens a real session, sends a real frame, reads a real result). A
  green run here means the device is ready to soak; it is not itself a soak test.

  Overlay permission (`SYSTEM_ALERT_WINDOW`) often cannot be granted via `adb shell appops set`
  on OEM ROMs (`SecurityException: uid 2000 does not have MANAGE_APP_OPS_MODES`). This is
  expected — the app falls back to a `SCREEN_BRIGHT_WAKE_LOCK` (`ScreenKeepAlive.Mode.WAKE_LOCK`)
  and functions correctly on that path too. Check `screen_keep_alive=true mode=...` in the
  `svc_started` log line to see which mode is active.

## 3. Running the connectivity soak test

```bash
./scripts/e2e_device_soak.sh [device_serial] [duration_seconds] [log_path]
# e.g. an 11-minute run (660s streaming + slack), matching the last verified pass:
./scripts/e2e_device_soak.sh 30940f89 660 .cursor/debug-$(date +%s).log
```

This is the automated form of the originally-requested manual mechanism:

```bash
adb install -r akshrava-debug.apk
adb logcat -c && adb logcat -s AkshravaDebug AkshravaVision > debug.log
# open app, configure endpoint/token/calibration, press Start
# use for N minutes with camera uncovered
# press Stop
# Ctrl+C the logcat
```

Beyond the manual mechanism, the script:

- **preflights before touching the device**: backend `/livez` + `/readyz` (derived automatically
  from repo `.env`'s `AKSHRAVA_WSS_URL`, same resolution the Android build itself uses), the app
  is actually installed, and — this one is easy to miss by hand — that `CAMERA` runtime
  permission is actually `granted=true`. A session can hold a perfectly healthy socket while the
  camera silently produces nothing; without this check that looks identical to success until you
  read `frames_sent` yourself.
- detects an ambiguous device set (`adb devices` shows more than one `device`) and refuses to
  guess which one to use, rather than nondeterministically picking the first.
- detects a locked screen (`mDreamingLockscreen=true`) before tapping — every subsequent tap on a
  locked screen "succeeds" (adb reports no error) while doing nothing, and would otherwise let a
  0-frame run report a false pass.
- locates Start/Stop by resource id via `uiautomator dump` (retried up to 3× against transient
  empty dumps), then **confirms the tap actually registered** by watching the log for `svc_start`
  / `onDestroy` rather than trusting the tap blindly — a missed hitbox reports the same "tap
  succeeded" exit code as a real one.
- captures **two** logcat channels: the tag-filtered app log, and a broad
  `AndroidRuntime:E ActivityManager:W` crash channel written to `<log>.crashes.log`. The app's
  own tags never carry a JVM crash or ANR — a real Kotlin exception is otherwise invisible next
  to "the process quietly stopped".
- ends the wait loop immediately (not at the full duration) if the process dies mid-soak, and
  treats that as a hard failure rather than silently waiting out the clock.
- guarantees the on-device session is torn down even on Ctrl-C / a script error: an interrupted
  script used to leave the camera, socket, and foreground service running on the phone
  indefinitely (verified live: PID stayed up after `kill -INT` before this was fixed). The exit
  trap force-stops the app whenever normal Stop-handling was not reached.
- writes an optional `RESULT_JSON=path` machine-readable summary alongside the human output.

**It exits 0 only if all of the following hold:**
- exactly one `event=connect_attempt` appears (the socket never had to reconnect);
- zero occurrences of the failure signatures in §4, and zero crash/ANR signatures in the crash log;
- the process never died before Stop was pressed;
- at least one frame was actually sent (not just "the socket said ready"); and
- at least half of sent frames got an answer back (`RESULT_JSON`/stdout report the exact ratio).

### Manual verdict, if reading a log by hand

```bash
L=.cursor/debug-<ts>.log

# The whole connection lifecycle in one view — should be ~6-8 lines for a clean run:
grep -E "AkshravaConnection|ws_ready|svc_start|onDestroy" "$L"

# Failure signatures (expect 0):
grep -cE "ws_drop|transport_drop|transport_failure|reconnect_scheduled|reconnect_executing|\
frame_slot_wedged|Assistance stalled|vision_unavailable|Connection restored|ws_hard_error" "$L"

# Throughput:
echo "sent:    $(grep -c 'frame_sent id=' "$L")"
echo "results: $(grep -c 'AkshravaVision: frame=' "$L")"
echo "dropped-to-send: $(grep -c 'sent=false' "$L")"

# Latency distribution (glass-to-ear, ms):
grep -oE "result_age_ms=[0-9]+" "$L" | cut -d= -f2 | sort -n | \
  awk '{a[NR]=$1} END {printf "n=%d p50=%d p90=%d max=%d\n", NR, a[int(NR*0.5)], a[int(NR*0.9)], a[NR]}'
```

A clean pass looks like this (actual output from the verified run):

```
07-22 12:36:32.152 svc_start endpoint=wss://...run.app/v1/session hasToken=true
07-22 12:36:32.345 event=connect_attempt generation=1 reconnectAttempt=0 replacedSocket=false
07-22 12:36:32.420 svc_started screen_keep_alive=true mode=OVERLAY
07-22 12:36:33.266 event=transport_open recovered=false
07-22 12:36:33.272 ws_ready detector=remote vision_enabled=true session_ready=true
07-22 12:47:42.167 event=client_close connectedForMs=668901
07-22 12:47:42.217 AssistService onDestroy stopping=true
```
One connect, one ready, one close — 668,901 ms (11m 08.9s) apart, driven by the Stop tap, not a
drop.

## 4. Failure signatures — what each one actually means

| Log signature | Meaning | Where in code |
|---|---|---|
| `ws_drop` / `event=transport_drop` | Socket died unexpectedly | `ProtocolClient.handleDrop` |
| `event=reconnect_scheduled` / `reconnect_executing` | Client is retrying — **any occurrence after the first connect is a real regression for a "stay connected" test** | `ProtocolClient.scheduleReconnect` |
| `frame_slot_wedged` | In-flight slot held past 15s — the settle-timeout safety net itself failed to fire | `AssistService.FRAME_SLOT_WEDGED_MS` |
| `Assistance stalled. Recovering.` | Wedge was caught and the slot force-released (self-healed, but log it — means the primary release path failed) | `AssistService.analyzeImage` |
| `vision_unavailable` / `Connection restored` flapping | Backend detector reported unavailable, client reconnected, still cycling | `ProtocolClient.handleMessage("error")` |
| `camera_stall rebind after=...` | Analysis callbacks stopped arriving for >15s; camera was rebound | `AssistService.cameraStallCheck` |
| `AssistService onDestroy` **without** a preceding `event=client_close` from the Stop tap | Service was killed (OOM, OEM background kill), not a user action | check `onTrimMemory`/`onLowMemory` lines just before |
| `event=stale_inference_tick` | A frame was sent and left unanswered past 3s. Bounded to 3 ticks per frame, so a burst of exactly 3 then silence is the watchdog working, not the session recovering | `ProtocolClient.shouldTickStaleInference` |
| `frame_luma ... occluded=true` repeating | Rear lens covered / phone lens-down. Frames are dropped from the **first** occluded frame; the spoken `Camera is dark. Uncover the rear lens.` prompt needs 3 consecutive | `AssistService.analyzeImage` · `FrameGate.isOccluded` |

## 5. Things that LOOK broken but are not (check here before filing a bug)

- **`frame_drop framePending` appearing dozens of times.** Camera analyzes at ~30 fps; the
  server admits ~1.2 fps. ~29 of every 30 frames are *supposed* to be shed here. Only worry if
  `held_ms` in the accompanying wedge check exceeds `FRAME_SLOT_WEDGED_MS` (15000).
- **A short burst of stale earcon ticks during a slow inference.** Capped at 3 per stuck frame on
  purpose. An unbounded tick becomes a permanent beep in the user's only audio channel, masking
  the alerts whose absence it is flagging; `FRAME_SETTLE_TIMEOUT_MS` (10 s) is the actual
  recovery. Zero ticks on a healthy low-battery session (0.2 FPS ⇒ 5 s capture interval) is also
  correct — the tick keys off an *outstanding frame*, never wall-clock since the last result.
- **Alerts moving to the phone speaker after the earbuds die.** Expected. `ACTION_AUDIO_BECOMING_NOISY`
  announces the route change and keeps speaking; it must never mute. If the session goes silent
  on unplug, that is a regression.
- **Detected objects but no spoken alert.** Only `VEHICLE_LABELS` and `OBSTACLE_LABELS`
  (`backend/akshrava_backend/hazards.py`) ever produce speech — a laptop, keyboard, chair, or
  TV is deliberately silent. Confirm with `scripts/watch_detection.sh <serial>`, which prints a
  live DETECTING column and explains this distinction inline.
- **No S1 (single-frame urgent) alerts ever fire.** Requires a *verified* calibration profile
  (`geometry_profile` present + `range_valid=true`). An uncalibrated `calibration_id` (e.g. the
  default `e2e-r0` unless explicitly upserted) can still produce S2 cautions after 2-frame
  tracker persistence, just never S1. See `scripts/upsert_calibration_profile.py`.
- **`camera_rebind_suppressed` / `camera_rebind_deferred` repeating.** The quality ladder can
  oscillate across a rung boundary continuously; a 10s cooldown (`MIN_QUALITY_REBIND_INTERVAL_MS`)
  intentionally suppresses most of these rather than actually rebinding CameraX each time (each
  real rebind costs 1-2s of frames). Only the handful of `camera_bound ok` lines are real binds.
- **Overlay permission grant fails over adb.** Expected on many OEM ROMs; see §2. Check for
  `mode=WAKE_LOCK` instead of `mode=OVERLAY` in `svc_started` — both are valid.
- **`alert_max_age_ms` in the `ready` payload is 8500, not 2500.** Deliberate:
  `cloud/gcp/app.tf` sets it higher for the CPU remote-YOLO path (`detector=remote` without GPU)
  to match its slower inference; GPU/noop paths keep the tight 2500ms boundary.

## 6. Debugging workflow when a real fault is found

1. **Reproduce with the soak script**, capturing a fresh log — do not debug from a partial or
   hand-copied log.
2. **Classify the failure** against §4 first. The signature tells you which file to open.
3. **Check `AgentDebugLog`** (`adb pull /sdcard/... ` is wrong — it's app-private; use
   `adb shell run-as org.akshrava.app cat files/agent-debug.ndjson`) if `debug_telemetry` was
   enabled at provisioning time. It carries structured per-hypothesis events (`H1`-`H7` tags in
   the code) that are cheaper to grep than raw logcat.
4. **Check server-side state independently** — a client-visible symptom (e.g. `worker_saturated`)
   often has a matching backend log line and `/metrics` counter
   (`akshrava_worker_saturated_total`, `akshrava_inference_failures_total`,
   `akshrava_late_suppressed_total`). Don't assume which side is at fault from the phone alone.
5. **Write the regression as a backend pytest or Android unit test before fixing**, not after —
   this repo's test suite (`backend/tests/`, `android/app/src/test`) is the primary way prior
   fixes stay fixed; see `backend/tests/test_websocket.py` and
   `android/app/src/test/java/org/akshrava/app/SessionDurationTest.kt` for the pattern (timing
   invariants between constants, not just "constant equals itself").
6. **Re-run the full suite** before considering it done:
   ```bash
   ./scripts/verify_phases.sh                                  # backend: pytest + ruff
   cd android && ./gradlew --no-daemon :app:testDebugUnitTest   # android unit tests
   ```

### Shell-scripting gotchas found while hardening the soak script itself

Worth knowing before writing another log-verdict script in this repo:

- **`$(grep -c PATTERN file || echo 0)` silently double-counts a genuine zero.** `grep -c`
  prints `"0"` on zero matches but still exits 1 (no match found), so the `|| echo 0` fallback
  fires too — the substitution captures both, giving `"0\n0"` instead of `"0"`. This produced a
  soak-script verdict that printed `failure signatures: 0\n0` on every clean, passing run — the
  *good* outcome broke the report. Fix: only default-to-zero on an empty result
  (`n=$(grep -cE ... 2>/dev/null); echo "${n:-0}"`), which correctly distinguishes "zero matches"
  (grep prints `0`) from "file didn't exist" (grep prints nothing).
- **A trap registered on both `EXIT` and `INT`/`TERM` re-enters itself.** Calling `exit` from
  inside the `INT` handler re-fires the `EXIT` trap on the same function, running the whole
  cleanup body twice (a Stop tap fired twice, a warning logged twice). Guard the handler with a
  `FINISHED` flag checked and set as the very first thing inside it.
- **A cleanup trap that only stops log capture is not "end to end."** The first version of
  `e2e_device_soak.sh` correctly stopped `logcat` on Ctrl-C but never tapped Stop — verified live
  that the assistance session (camera + socket + foreground service) kept running on the phone
  indefinitely after the *script* had already exited. Any harness that drives a stateful session
  on a device needs its interrupt path to tear down that session, not just its own local capture.

## 7. Quick reference — one-shot commands

```bash
# Backend health
curl -s https://<host>/livez
curl -s https://<host>/readyz

# Full provision + live-path proof (idempotent, safe to re-run)
GOOGLE_APPLICATION_CREDENTIALS=<sa.json> ./scripts/install_android_debug_full.sh [serial]

# Protocol-level soak (no phone needed — simulates the wire protocol directly)
AKSHRAVA_WSS_URL=wss://host/v1/session AKSHRAVA_TOKEN=<jwt> \
  python3 scripts/soak_session.py --minutes 15

# Real-device soak (drives the actual installed app)
./scripts/e2e_device_soak.sh [serial] [seconds] [log_path]
./scripts/e2e_device_soak.sh --help                                    # full flag/env reference
RESULT_JSON=result.json ./scripts/e2e_device_soak.sh 30940f89 660       # + machine-readable summary

# Live "is it actually detecting anything right now" view
./scripts/watch_detection.sh [serial]

# Read back what got provisioned onto the device (Keystore-encrypted token, not plaintext)
adb shell run-as org.akshrava.app cat shared_prefs/akshrava.xml
```

## 8. Related project memory

Prior sessions' findings, kept in the auto-memory system rather than restated here since they
decay: `android-gcp-e2e-provisioning` (full working chain: endpoint resolution, token minting,
Keystore persistence) and `detection-pipeline-diagnosis` (why "no alert" is usually correct
behavior, not a broken pipeline). Both are point-in-time snapshots — re-verify against current
code before trusting a specific file/line they cite.
