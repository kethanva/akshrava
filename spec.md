# spec.md — Session longevity fix

**Status:** implemented in commit `114858c`; this spec documents the behavior and the acceptance
bar. Later releases retain the same session-longevity contract.

## Problem

A supervised walk regularly runs longer than an hour, and users reported assistance going silent
after several minutes to an hour **with the WebSocket still open and nothing in any log** to
explain it. Three independent causes, all producing the same "it just stopped" symptom:

1. **Android — timed wake locks lapse mid-walk.** The CPU `PARTIAL_WAKE_LOCK` and the
   screen-bright fallback are acquired with a 1-hour timeout as a *safety net against hung
   teardown*, but that net was also acting as a *session budget*. When it expired the display
   slept; on OEM ROMs CameraX then stops delivering frames. The socket stayed open, so nothing
   looked broken.
2. **Backend — frame analysis was fire-and-forget.** `asyncio.create_task(process_frame())` is
   only weakly referenced by the loop, so a task could be GC'd mid-inference. The phone then never
   got a result for a frame it believed was in flight, and its single in-flight slot stayed held
   until a 10 s settle timeout — a silent stall. Concurrent tasks also interleaved `SessionState`
   mutation (dropping the second hit S2 needs) and returned results out of capture order.
3. **Android — the watchdog recovery prompt could ANR or be unintelligible.** The `goAsync()`
   broadcast could stay open past the ~10 s receiver-ANR limit when the TTS engine (possibly the
   one an OEM ROM just force-stopped) never fired an utterance callback; and `Locale("hi-IN")`
   produced a non-ISO language so a Hindi-only user heard English.

## Requirements

### Must
- A healthy session (frames still flowing) keeps its CPU and screen wake locks alive
  **indefinitely**, re-arming well inside their own timeout.
- A session that has genuinely stopped producing frames must **let the locks lapse on schedule**
  (so the watchdog can detect the stall) — renewal is driven by frame activity, not a wall timer.
- Wake-lock renewal must be leak-free: repeated `acquire` must not accumulate holds a single
  `release` cannot balance.
- Backend frame analysis runs **off the receive loop** (phone stays able to send Stop/ping mid
  inference) but **strictly one frame at a time per connection**, preserving capture order and
  serialized `SessionState` mutation.
- Analysis tasks are **tracked** (never fire-and-forget) and **cancelled + awaited before session
  state is released** on socket teardown.
- Transient/circuit inference failures **shed the frame and keep the socket** (no reconnect flap);
  only a genuine unexpected error closes the socket (1011).
- The watchdog recovery broadcast **always finishes exactly once**, before the ~10 s ANR limit,
  on every path (init failure, utterance rejected, onDone, onError, timeout).
- The watchdog prompt speaks in the user's **provisioned BCP-47 language**, falling back to a
  speakable default when the tag is blank.

### Should
- Store shutdown uses redis-py `aclose()` (not the deprecated `close()` alias), matching the other
  Redis-backed components.
- Remove client state that implies an unsupported capability (`ProtocolClient.maxInFlight`): the
  client honors exactly one in-flight frame; the server's advertised value is logged, not stored.

### Won't (this change)
- No auto-restart of a dead session — the watchdog stays **prompt-only**; the user must visibly
  press Start (platform rule).
- No support for >1 in-flight frame per connection.
- Release-version parity work (`check_release_version.py` / `release.yml`) — landed in the same
  commit.
- No overlay-permission acquisition changes; the overlay path already holds
  `FLAG_KEEP_SCREEN_ON` with no expiry.

## Interface (as implemented)

Android — `AssistService`:
```kotlin
internal const val HEARTBEAT_INTERVAL_MS       = 30_000L
internal const val WAKE_LOCK_TIMEOUT_MS        = 60 * 60_000L   // safety net, not a budget
internal const val WAKE_LOCK_RENEW_INTERVAL_MS = 15 * 60_000L   // re-arm inside the net
// wakeLock.setReferenceCounted(false); re-acquire(WAKE_LOCK_TIMEOUT_MS) on renew
private fun maybeRenewWakeLocks(now: Long)   // called from the heartbeat (camera-analysis driven)
```
Android — `ScreenKeepAlive`:
```kotlin
fun renew()   // re-arms only Mode.WAKE_LOCK; overlay mode is no-expiry, so no-op there
```
Android — `WatchdogReceiver` (`internal companion object`):
```kotlin
const val SPEECH_TIMEOUT_MS = 6_000L                 // < 10 s goAsync ANR budget
fun speechLocale(languageTag: String): Locale        // Locale.forLanguageTag, "" -> "en-IN"
// goAsync() finish guarded by AtomicBoolean(settled) -> exactly one shutdown()+finish()
```
Backend — `main.py`:
```python
async def _analyze_and_reply(websocket, state, header, jpeg, decode_ms, device_id,
                             frame_lock: asyncio.Lock) -> None
# in session(): frame_tasks = BackgroundTaskTracker("frame-analysis:%s" % session_id)
#               frame_lock  = asyncio.Lock()
#               frame_tasks.schedule(_analyze_and_reply(...))     # tracked, not create_task
#               finally: await frame_tasks.cancel_all()           # before releasing state
```
Backend — `service.py`:
```python
async def cancel_all(self) -> None   # cancel every tracked task, await unwind, no grace period
```
Backend — `storage.py`: `Store.close()` awaits `self._redis_client.aclose()`.

## Edge cases
- Renewal missed once (a skipped heartbeat) must not lapse the lock → renew interval ≤ ½ timeout.
- Heartbeat must fire many times per renewal window → `HEARTBEAT_INTERVAL_MS * 4 ≤ RENEW_INTERVAL`.
- TTS engine initialises but rejects the utterance (`speak() != SUCCESS`, the "not bound to TTS
  engine" state after a force-stop): finish immediately, don't wait for the timeout.
- TTS accepts then never reports done: the timeout handler finishes the broadcast.
- Blank / whitespace provisioned language → `en-IN` locale (which exposes `"en"` as its `.language`), never a crash or silent English.
- First frame of a session writes the calibration row (a cost later frames skip) — must not let a
  later frame's result overtake it → serialized by `frame_lock`.
- Socket closes with a frame in flight: `cancel_all()` runs in `finally` **before**
  `session_admission.close()` and state release, so no task touches a torn-down tracker/lease.

## Acceptance criteria (all via existing tests unless noted)
- `SessionDurationTest.wakeLocksAreRenewedFarInsideTheirOwnTimeout`: `RENEW*2 ≤ TIMEOUT` and
  `HEARTBEAT*4 ≤ RENEW`.
- `WatchdogRecoveryPromptTest`: every `SupportedLanguages` tag resolves to its ISO `wireCode`;
  blank/whitespace → `en` (as resolved by `.language`); test asserting `SPEECH_TIMEOUT_MS ≤ 8_000` to verify it stays safely under the 10s ANR limit, while implemented as `6_000L`.
- `test_websocket.test_pipelined_frames_are_answered_in_capture_order` — results in capture order.
- `test_websocket.test_concurrent_frames_do_not_interleave_session_state` — S2 second hit survives.
- `test_service.test_cancel_all_stops_tracked_work_that_has_nowhere_left_to_go` — task cancelled,
  not completed, all done; and `..._is_safe_when_nothing_is_in_flight`.
- `test_storage` — Store.close awaits `aclose()`.
- Required automated gates: `PYTHON_BIN=python3.12 ./scripts/test_backend.sh` and
  `./gradlew :app:testDebugUnitTest`; CI must keep both green. This specification records the
  acceptance commands rather than claiming that every workstation has rerun both gates.
- Manual field gate: run a 65-minute soak when sign-off requires direct evidence of the
  15-minute `maybeRenewWakeLocks` re-arm (`./scripts/e2e_device_soak.sh <serial> 3900` or
  `./scripts/e2e_device_soak.sh "" 3900`; see `AGENT.md`). The invariant is **display never
  sleeps until the app is stopped**. The repository's last recorded run is 11 minutes 08.9
  seconds, so this document does not claim that the 65-minute gate has passed.
