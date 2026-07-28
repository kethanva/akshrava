# plan.md — Session longevity fix

## Goal
Land the session-longevity fix (see `spec.md`): a healthy walk stays connected and speaking
indefinitely; a genuinely dead one is caught by the watchdog and recovers via a spoken,
correctly-localized prompt — with green tests and a soak run past the old failure window.

## Reality check on state
The three code changes are **committed** as `114858c`, with tests:
- Android: `AssistService.kt`, `ScreenKeepAlive.kt`, `WatchdogReceiver.kt`, `ProtocolClient.kt`
  (+ `SessionDurationTest.kt`, new `WatchdogRecoveryPromptTest.kt`).
- Backend: `main.py`, `service.py`, `storage.py` (+ `test_websocket.py`, `test_service.py`,
  `test_storage.py`).

The implementation is landed. The remaining risk is in *validating* it on real hardware
(concurrency, real-device wake-lock behavior).

## Steps (each independently reviewable)

1. **Confirm scope boundary.** Separate the two unrelated themes in the tree:
   ```bash
   git stash push -m "version-parity" scripts/check_release_version.py .github/workflows/release.yml backend/tests/test_release_version.py
   ```
   Keep the session-longevity files (above) for this change. Decide whether `storage.py` `aclose()` rides with this change (it is shutdown-path correctness — yes) vs. the version work (no). Use `git add -p backend/akshrava_backend/storage.py` to selectively stage only the `aclose()` changes. Keep `spec.md`, `plan.md`, and `AGENTS.md` untracked.

2. **Read-review the backend concurrency invariant.** Re-verify by hand that `frame_lock`
   serializes *all* `SessionState` mutation and result sends, and that `cancel_all()` in the
   `finally` runs strictly before `session_admission.close()` / state release. This is the
   highest-risk correctness claim.

3. **Run backend gates.** Recreate the stale 3.8 venv, then:
   `rm -rf backend/.venv && PYTHON_BIN=python3.12 ./scripts/test_backend.sh`. Must pass pytest
   (incl. the two new `test_websocket` cases + `test_service` cancel_all) and `ruff check`.

4. **Run Android unit gates.** `cd android && ./gradlew :app:testDebugUnitTest`. Must pass,
   including `SessionDurationTest` renewal-margin asserts and all `WatchdogRecoveryPromptTest`.

5. **Targeted code review.** Run the `code-reviewer` and `silent-failure-hunter` agents over the
   diff, focused on: wake-lock leak safety (`setReferenceCounted(false)` + single release),
   `goAsync()` finish idempotency (`AtomicBoolean` covers every path incl. timeout race), and the
   backend shed-vs-close error taxonomy. Address CRITICAL/HIGH before landing.

6. **On-device soak — 65 min.** First run `adb devices` to find your device serial. Then run: `./scripts/e2e_device_soak.sh <serial> 3900` (if you omit the serial, you must pass `""` as the first argument: `./scripts/e2e_device_soak.sh "" 3900`). Pass bar:
   display stays on the whole run, sustained `frame_sent`/result flow, zero reconnects, socket
   alive until Stop. Invariant to falsify: display must **not** sleep at any point before Stop.
   This 65-minute duration is strictly required to verify the 15-minute `maybeRenewWakeLocks` re-arm, as the initial wake lock holds for 1 hour.

7. **Commit + PR.** One focused commit (`fix: keep long walks alive — wake-lock renewal, tracked
   single-flight frame analysis, ANR-safe localized watchdog prompt`). Branch off `main` first via `git checkout -b fix/session-longevity`.
   PR body: problem, the three root causes, test evidence, soak evidence.

## Out of scope
- Auto-restarting a dead session (watchdog stays prompt-only).
- Supporting >1 in-flight frame per connection.
- Release-version parity (`scripts/check_release_version.py` / `release.yml`) — separate change.
- Overlay-permission acquisition changes.
- Any navigation/crossing/collision/"safe" behavior (permanent boundary).

## Decisions
- **Soak = 65 min (3900s).** Acceptance invariant: **display never sleeps until the app is stopped.** This replaces the insufficient 10-minute soak test to properly verify the `maybeRenewWakeLocks` behavior.

## Open risks / questions
- **OEM wake-lock behavior is device-specific.** "Display never sleeps" is only as good as the
  ROM tested. Which donated phone is authoritative for sign-off? (soak default earlier: OnePlus7T
  `30940f89`.)
- **`storage.py aclose()`** — confirm no code path still calls the old `close()` alias elsewhere.

---
**Stopping here for your review — no code touched.** Confirm the plan (and the two soak questions
above) before I run any gates or agents.
