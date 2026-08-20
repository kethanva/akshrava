# plan.md — Session longevity implementation record

**Status:** implemented in commit `114858c` and carried forward through the current `v0.2.14`
release. This file records the intended change and the evidence still required for field use; it
is no longer a pending implementation checklist.

## Goal

Keep a healthy Android walk session connected and speaking indefinitely, while making a genuinely
stalled session observable and recoverable through an explicit, localized watchdog prompt. The
implementation must preserve one-frame-in-flight backpressure, bounded inference, serialized
session state, and the project's awareness-only boundary.

## Implemented changes

- Android wake locks and screen keep-alive renew from real frame activity, not wall-clock time;
  renewal is non-reference-counted and teardown is balanced.
- Backend frame analysis runs off the receive loop, is tracked per session, serialized per
  connection, and cancelled/awaited before session state is released.
- Transient inference failures shed only the affected frame; circuit escalation is separate from
  transport teardown.
- Watchdog speech has a bounded timeout, exactly-once completion, and provisioned-language
  fallback.
- Release-version parity and the migration-revision gate are enforced by CI.

## Verification already available

Run from the repository root:

```bash
PYTHON_BIN=python3.12 ./scripts/test_backend.sh
cd android && ./gradlew :app:testDebugUnitTest
```

The broader baseline is `./scripts/verify_phases.sh`; it runs the backend suite, Phase-0 replay,
and Ruff. The Android build/lint and iOS simulator gates are defined in CI. A full Xcode toolchain
is required to execute the iOS framework-backed target; Command Line Tools can only parse the
sources and run the explicit safety/version checks.

## Remaining field evidence

The code and automated tests do not replace a physical-device soak. Before a supervised trial,
run the current device procedure in [`AGENT.md`](AGENT.md), including a 65-minute run when the
15-minute wake-lock renewal interval itself must be evidenced. The last run recorded in that
playbook is 11 minutes 08.9 seconds; this repository does not claim that the 65-minute soak has
been completed.

The field pass requires sustained frame/result flow, no unexpected reconnect, display continuity
until explicit Stop, intelligible watchdog/status speech, and accessible Stop/Mute/Repeat behavior.

## Explicit non-goals

- No automatic restart of a dead session; the user must explicitly press Start.
- No support for more than one in-flight frame per connection.
- No overlay-permission redesign.
- No navigation, crossing, collision, approach-speed, clear-path, or safety-guarantee behavior.

## Related specifications

- [`spec.md`](spec.md) — detailed requirements and acceptance criteria.
- [`AGENT.md`](AGENT.md) — device soak and failure-signature playbook.
- [`Important Architecture.md`](Important%20Architecture.md) — release gates and supervised-trial
  boundary.
