# Akshrava feature triage

**Last researched:** 2026-07-30  
**Platform floor:** `minSdk 26` (Android 8.0) → `compileSdk 36`. Field phones: API 28–36 ([`AndroidSupportMatrix`](android/app/src/main/java/org/akshrava/app/AndroidSupportMatrix.kt)).  
**Safety boundary:** object/vehicle *awareness only*. Never navigation, crossing decisions, collision avoidance, approach-speed, clear-path, distance-to-object guidance, or "safe" guarantees — in code, strings, TTS, tests, or docs.  
**Hard deferrals:** [`NOT_NOW.md`](NOT_NOW.md) · field architecture: [`Important Architecture.md`](Important%20Architecture.md).

This file is the live product backlog. Every backlog row must stay API 26+, safety-clean, and either JVM-testable or soak-signed.

---

## 0. How this was rebuilt (end-to-end)

| Step | What was done |
|---|---|
| Code audit | Wired modules under `android/app/src/main/java/org/akshrava/app/` + matching `*Test.kt`; backend hazard labels in `hazards.py` / `composer.py` |
| Commercial BLV apps | Primary docs: [Seeing AI Play](https://play.google.com/store/apps/details?id=com.microsoft.seeingai), [Seeing AI Android channels](https://blogs.microsoft.com/accessibility/seeing-ai-app-launches-on-android-including-new-and-updated-features-and-new-languages/), [Lookout Help](https://support.google.com/accessibility/android/answer/9031274), [Lookout Play](https://play.google.com/store/apps/details?id=com.google.android.apps.accessibility.reveal), [Envision](https://www.letsenvision.com/app), [Be My Eyes](https://www.bemyeyes.com/) |
| User-study evidence | ITU / arXiv [2407.17496](https://arxiv.org/pdf/2407.17496) — Seeing AI, Supersense, Envision, Lookout scored on accuracy, latency, reliability, accessibility, privacy, **energy**, usability |
| Open source / platform patterns | [AChep/PocketMode](https://github.com/AChep/pocketmode) + AOSP-style pocket (proximity + light); [Observa](https://github.com/jose2505207-eng/observa) (on-demand OCR, priority speech); [AccessLens](https://github.com/hassaninnovate/AccessLens) (earcons, lanyard walk); [google-research/project-guideline](https://github.com/google-research/project-guideline) (**path guidance — reject**) |

**Akshrava niche (do not dilute):** supervised **walking awareness** on recycled phones (low FPS, cloud YOLO, cane/guide primary). Daily-living tools (OCR, INR, barcode, color) are **on-demand modes**, never the continuous walk loop — matching Lookout’s mode spinner and Seeing AI’s channels, not Project Guideline / Find-with-distance.

---

## 1. Research → product implications

| Source (primary) | What they ship | Copy into Akshrava | Explicitly refuse |
|---|---|---|---|
| **Seeing AI** ([Play](https://play.google.com/store/apps/details?id=com.microsoft.seeingai), [blog](https://blogs.microsoft.com/accessibility/seeing-ai-app-launches-on-android-including-new-and-updated-features-and-new-languages/), [Sight for Surrey Android guide](https://sightforsurrey.org.uk/wp-content/uploads/2025/05/An-Introduction-to-Seeing-AI-Android.pdf)) | Channels: Short Text, Documents, Products/barcode, Scenes, People, Currency, Colors, Handwriting, **Light** (tone pitch ↔ brightness) | Light *status* / optional tone; Colors; on-demand OCR/barcode/currency; scene on request | Continuous Short Text while walking; any “go / clear” implication |
| **Google Lookout** ([Help](https://support.google.com/accessibility/android/answer/9031274), [Play](https://play.google.com/store/apps/details?id=com.google.android.apps.accessibility.reveal)) | Modes: Text, Documents, Explore, Currency (**USD / EUR / INR**, notes only), Food labels/barcode, Find (doors, seats, vehicles…), Images; optional auto flashlight; haptics | On-demand text / INR / barcode; door **label** awareness; flashlight for demand capture | Find’s *“direction and distance guidance to the object”* (Help Center wording) — forbidden geometry |
| **Envision** ([site](https://www.letsenvision.com/app)) | OCR 60+ langs, Describe Scene, color, barcode, enrolled faces, Ask Envision | On-demand OCR / scene / color | Face ID as continuous walk alert; Ask-AI as primary walking path |
| **Be My Eyes** | Volunteer video + Be My AI | Opt-in remote help **P3+** only | Volunteer link as required mid-walk dependency |
| **arXiv 2407.17496** ([PDF](https://arxiv.org/pdf/2407.17496)) | Users rate accuracy under **variable light**, energy drain, reliability, TalkBack, privacy | Prioritize pocket suspend, lighting guards, AudioFocus (done), INR quality later | Features that keep camera+upload hot on 2 GB donated phones |
| **Open source** | PocketMode / AOSP pocket = proximity + light; Observa = on-demand OCR; AccessLens = earcons; Project Guideline = path tones | Pocket FSM; earcon-only urgent; still OCR | Path / veer / “move right” guidance (Guideline, GlassInterface-style risk scorers) |

---

## 2. Already shipped (verified wired, API 26+)

Do not rebuild. Extend tests / soak signals instead.

| ID | Feature | Evidence in tree | Acceptance signal |
|---|---|---|---|
| F-01 | Directional haptics | [`HapticFeedbackEngine`](android/app/src/main/java/org/akshrava/app/HapticFeedbackEngine.kt) ← `AlertManager.deliverAnnounce` | Hazard → left / ahead / right vibration pattern |
| F-02 | Lens occlusion / darkness | [`FrameGate.isOccluded`](android/app/src/main/java/org/akshrava/app/FrameGate.kt) (mean luma < 8) + `AssistService` — drop is immediate, prompt waits ≥3 frames | `Camera is dark. Uncover the rear lens.` (8 s cooldown); dim-but-usable scenes (luma 8–20) still stream |
| F-03 | Extreme tilt awareness | [`PoseTracker`](android/app/src/main/java/org/akshrava/app/PoseTracker.kt) `EXTREME_PITCH_CDEG` + `AssistService.maybeAnnounceTilt` + [`PoseTrackerTest`](android/app/src/test/java/org/akshrava/app/PoseTrackerTest.kt) | `Phone tilted. Point camera forward.` after ≥2 s hold |
| F-05 | Headset shortcuts | [`HeadsetControls`](android/app/src/main/java/org/akshrava/app/HeadsetControls.kt) MediaSession | Single=repeat, double=mute 15 m, long-press=look |
| F-06 | Thermal / power throttle | `AssistService.maybeCheckThermal` + [`CapturePolicy`](android/app/src/main/java/org/akshrava/app/CapturePolicy.kt) | Cool-down / battery-low FPS |
| F-07 | Connection earcons | [`ConnectionEarcons`](android/app/src/main/java/org/akshrava/app/ConnectionEarcons.kt) via [`ProtocolClient`](android/app/src/main/java/org/akshrava/app/ProtocolClient.kt) + [`ConnectionEarconsTest`](android/app/src/test/java/org/akshrava/app/ConnectionEarconsTest.kt) | Tones: open / drop / restore / stale / look-fail / reconnect |
| F-30 | Stale-inference tick | `ProtocolClient.shouldTickStaleInference` + [`StaleInferenceWatchdogTest`](android/app/src/test/java/org/akshrava/app/StaleInferenceWatchdogTest.kt) | Keyed on an **outstanding frame**, not wall-clock (capture legitimately runs at 0.2 FPS); max 3 ticks per frame, then silent — the settle timeout is the real recovery |
| F-09 | AudioFocus ducking | [`AlertAudioFocus`](android/app/src/main/java/org/akshrava/app/AlertAudioFocus.kt) + `AlertManager` `focusHoldCount` + [`AlertAudioFocusTest`](android/app/src/test/java/org/akshrava/app/AlertAudioFocusTest.kt) | Duck media/TalkBack; abandon only after last utterance |
| F-15 | Auditory battery gauge | [`DeviceCapability.batteryStatusText`](android/app/src/main/java/org/akshrava/app/DeviceCapability.kt) on the `AssistService` low-battery warning + [`BatteryGaugeTest`](android/app/src/test/java/org/akshrava/app/BatteryGaugeTest.kt) | Spoken `%` + estimated hours, always marked `Estimated`, never “0 hours” |
| F-17 | BT / headset disconnect notice | `ACTION_AUDIO_BECOMING_NOISY` → `onAudioRouteLost` in HeadsetControls (`RECEIVER_NOT_EXPORTED` on API 33+) | Unplug → `Headset disconnected. Alerts now play on the speaker.`; alerts keep flowing (**must not mute** — silence is indistinguishable from a dead app) |
| F-18 | Regional languages | [`SupportedLanguages`](android/app/src/main/java/org/akshrava/app/SupportedLanguages.kt) + backend `composer.py` | `en/hi/ta/kn/ml/te` |
| F-19 | Stationary throttle | PoseTracker `MotionState` + CapturePolicy `STATIONARY_FPS = 1.0` | Standing still still allows S2 second hit |
| F-20 | Privacy debug beacon | [`AgentDebugLog`](android/app/src/main/java/org/akshrava/app/AgentDebugLog.kt) | App-private NDJSON when `debugTelemetry` |
| — | Session watchdog (distinct from F-30) | [`Watchdog`](android/app/src/main/java/org/akshrava/app/Watchdog.kt) + WatchdogReceiver | Prompt-only; never auto-starts FGS |
| F-31 | Double-shake | [`GestureDetectorEngine`](android/app/src/main/java/org/akshrava/app/GestureDetectorEngine.kt) in AssistService + [`GestureDetectorEngineTest`](android/app/src/test/java/org/akshrava/app/GestureDetectorEngineTest.kt) | Shake → one look; 3 s trigger cooldown; speaks nothing extra (an extra utterance would flush a live hazard alert) |
| F-42 | Glare / washout guard | [`FrameGate.isGlared`](android/app/src/main/java/org/akshrava/app/FrameGate.kt) + AssistService (≥3 frames, drop) + [`FrameGateTest`](android/app/src/test/java/org/akshrava/app/FrameGateTest.kt) | `Camera blinded by light. Turn slightly.` |
| F-71 | Ambient light **edge** context | [`AmbientLightMonitor`](android/app/src/main/java/org/akshrava/app/AmbientLightMonitor.kt) (`TYPE_LIGHT` @ 1 Hz) + `AssistService.announceAmbientLightEdge` + [`AmbientLightMonitorTest`](android/app/src/test/java/org/akshrava/app/AmbientLightMonitorTest.kt) | Edges only: `Environment is dark.` / `Brighter now.` after a 3 s hold, ≥8 s cooldown; 10/50 lux hysteresis band; no continuous tone; dropped (never deferred) while a hazard alert is still landing |
| F-72 | Wipe-lens blur prompt | `FrameGate.shouldAnnounceBlur` + AssistService + [`FrameGateTest`](android/app/src/test/java/org/akshrava/app/FrameGateTest.kt) | `Camera is blurry. Wipe the lens. Use cane or guide.` after ≥5 frames; **never drops**; first prompt is not held behind the 60 s cooldown |
| — | Priority look (baseline) | Protocol `priority` / headset long-press → `look_summary` | On-demand scene sentence; no approach/crossing |
| — | Backend awareness classes | [`hazards.py`](backend/akshrava_backend/hazards.py) vehicles + obstacles (`person`, `pole`, …) | `vehicle_nearby` / `person_ahead` / `obstacle_ahead` only |

### Partial (already in walk loop — do not treat as greenfield)

| Piece | Today | Remaining polish |
|---|---|---|
| Blur gate | `FrameGate.isBlurred` → `FrameGate.shouldAnnounceBlur`: after **≥5** frames, `Camera is blurry. Wipe the lens. Use cane or guide.`, then a **60 s** cooldown — **never drops** frames | None. F-72 shipped: copy names the fix, and the first prompt no longer sits behind the cooldown (a `0` sentinel against `elapsedRealtime()` swallowed it on a freshly booted phone) |

---

## 3. Prioritized backlog (build strictly in order)

Priority ranking uses: (1) energy / session survival on donated phones, (2) lighting reliability from arXiv, (3) noisy-street accessibility, (4) India field fit (INR, regional TTS already shipped), (5) safety distance from guidance.

### P0 — Walk-session reliability (next engineering)

| # | ID | Feature | Why now (evidence) | Exact work | Acceptance |
|---|---|---|---|---|---|
| 1 | **F-73** | In-pocket suspend | Pocketed rear-camera sessions burn heat/battery while “looking healthy”; AssistService already comments on pocket-as-busy-loop risk; PocketMode / AOSP = `TYPE_PROXIMITY` + `TYPE_LIGHT` | New `PocketStateDetector` (prox near + lux ≲ ~3 + optional face-down gravity); AssistService pauses analysis / sheds frames; status `Assistance paused. Phone may be in a pocket.`; resume on exit | Unit: pocket FSM. Soak: pocket 30 s → pause; remove → frames resume |
| 2 | **F-04** | Earcon-only urgent mode | Noisy Indian streets; Seeing AI Light uses tones; we already have `ConnectionEarcons` | Pref on `AppConfig` + MainActivity toggle; S1 → earcon + haptic only; status / look still spoken | Unit: urgent×pref matrix. Manual: traffic noise, S1 buzz without sentence |

> F-71 and F-72 have shipped — see §2. F-73 inherits the `TYPE_LIGHT` registration pattern from
> [`AmbientLightMonitor`](android/app/src/main/java/org/akshrava/app/AmbientLightMonitor.kt), but
> needs its own reading: pocket detection wants raw lux against a near-zero floor, not the
> dark/bright edge machine, and it must gate on proximity as well.

### P1 — On-demand daily living (user-triggered only)

Aligns with Lookout modes / Seeing AI channels / Envision tools and [`NOT_NOW`](NOT_NOW.md) OCR rule (*single still*, not stream).

| # | ID | Feature | Why | Exact work | Acceptance |
|---|---|---|---|---|---|
| 5 | **F-75** | Color name | Seeing AI Colors; Envision Detect colors; **zero server cost** | Headset triple-press or mode → center YUV → spoken bucket (EN + HI first) | Unit: YUV→label. Manual: red card / blue wall |
| 6 | **F-10** | Still OCR (“read sign”) | Lookout Text; Seeing AI Documents; NOT_NOW Phase 4+ | Higher-side still; ML Kit Text and/or backend OCR; speak once; walk FPS unchanged | E2E: one still → text |
| 7 | **F-76** | Barcode / package | Lookout Food labels; Seeing AI Products; Envision barcode | On-device ML Kit Barcode in demand mode | Unit: fixture bytes. Manual: one grocery code |
| 8 | **F-21** | INR currency | Lookout Currency = USD/EUR/**INR**; arXiv notes INR denomination accuracy is hard | Notes only (no coins); TFLite / curated model; indoor light first | Manual: ₹100 / ₹500 |
| 9 | **F-12** | Gait / sway dampening | Lanyard bounce → false tilt / motion | Reject pose updates when `accelMad` spikes without turn; EMA | Unit: synthetic IMU |

### P2 — Server awareness expansions (still walk-safe)

| # | ID | Feature | Why | Exact work | Acceptance |
|---|---|---|---|---|---|
| 10 | **F-25** | Door / entrance **label** | Lookout Find lists doors — we announce **presence only** | YOLO `door` (+ open/closed if model allows); `message_key=door_ahead`; never “go through” / “clear” | Backend pytest + Android templates |
| 11 | **F-74** | Crowd density awareness | Dense sidewalks; person boxes already scored | ≥N persons → `Crowded area ahead` (S2, long cooldown); no wait/go | Pytest threshold |
| 12 | **F-08** | Static GPS caution bookmarks | NOT_NOW Phase 4; urban GPS ±5–15 m | Opt-in location; local points; `Known caution nearby` only; no trajectories | Unit: geofence hysteresis |
| 13 | **F-77** | Richer on-demand scene (VLM) | Seeing AI “more info”; Lookout Images / Be My AI | User-triggered still → cloud VLM once; timeout + cane fallback | Latency budget; walk loop untouched |
| 14 | **F-78** | Demand-mode torch assist | Lookout “Automatic flashlight” setting | Optional torch only during OCR/currency/look still; never as “path is lit / clear” | Unit: mode×torch policy |

### P3 — Optional / hardware-gated (after P0–P1 soak)

| ID | Feature | Gate |
|---|---|---|
| F-38 | Proximity micro-bumper haptic | After F-73 pocket FSM is solid (shared prox sensor) |
| F-44 | Remote volunteer / Be My Eyes-style | Explicit opt-in; WebRTC; privacy review |
| F-40 / F-65 | Wear OS / HR stress filter | Rare on donated phones |

---

## 4. Rejected or deferred

Do not implement from this document until [`NOT_NOW.md`](NOT_NOW.md) preconditions pass and a mobility-specialist review exists where required.

| Idea / IDs | Reason |
|---|---|
| Lookout **Find direction + distance**; Project Guideline path tones; “Person approaching, X metres — move right” | Geometry / guidance — **safety boundary** |
| Continuous Short Text / stream OCR while walking | NOT_NOW; Lookout Text is a **mode**, not walk default |
| F-24 sonar, F-28 stair “prevent missteps”, F-32 traffic-signal crossing, F-47 crosswalk guidance, F-57 path-boundary hums, F-61 “ramps are safe”, F-68 rear approach / TTC | Safety boundary / NOT_NOW research gates |
| Optical-flow looming, local “approaching” tracker, vehicle TTC | NOT_NOW; Insufficient FPS + ego-motion |
| F-51 SLAM, F-52 on-device LLM, F-56 continuous VQA, F-59 ESP32, F-63 ToF, F-64 ASL, F-69 medical gait, F-70 IR | Extreme cost or rare hardware on recycled phones |
| F-35 enrolled face as continuous walk alert | Privacy + false confidence |
| iOS port | NOT_NOW Phase 4+ |
| Invented modules with no tree base (`CautionPointStore`, `ObjectFinder`, `SlamMemory`, …) | Speculative |

---

## 5. Safety rewrite rules (any new string)

**Allowed examples:** `Vehicle nearby, left` · `Camera is dark. Uncover the rear lens.` · `Phone tilted. Point camera forward.` · `Assistance paused. Phone may be in a pocket.` · `Crowded area ahead` · `Door ahead` · `Camera is blurry. Wipe the lens.`

**Forbidden examples:** safe to cross · clear path · approach speed · time-to-contact · “go ahead” / “walk now” · distance-to-object guidance · “ramps are safe” · “move left/right to avoid”

---

## 6. Verification

```bash
cd android && ./gradlew :app:testDebugUnitTest
./scripts/e2e_device_soak.sh [serial] 660   # after device provision
# Backend when protocol / hazard labels change:
PYTHON_BIN=python3.12 ./scripts/test_backend.sh
```

**Rollout rule:** land **one P0 row at a time**; soak ≥10 min on a donated API 28–29 phone before starting the next ID. Do not start P1 OCR/currency until pocket + earcon-only are soak-green — energy and street audibility beat daily-living modes for walking sessions.
