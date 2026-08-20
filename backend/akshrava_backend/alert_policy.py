"""Application-layer alert delivery policy.

Hazard scoring is deliberately pure.  This policy owns the user/session-specific side effects
that decide whether an already-scored hazard is allowed onto the wire.

The phone AlertManager owns the 5 s per-object speech cooldown and the 2 s utterance gap.
This policy only debounces same-key re-admits across consecutive frames (~800 ms) and applies
a coarse global backstop so a saturated detector cannot flood the socket. Urgent / S1 hazards
are exempt from the global cap so a busy corridor cannot silently drop the highest-severity cue.
"""

import time

from .domain import Hazard, SessionState

# Short same-key debounce only. Speech cooldowns live on the phone so a client drop
# (utterance gap / busy collapse) cannot create a multi-second phantom silence.
ALERT_DEBOUNCE_MS = 800
GLOBAL_RATE_LIMIT = 6
GLOBAL_RATE_WINDOW_MS = 60_000


class AlertPolicy:
    def admit(self, state: SessionState, candidate: Hazard | None, *, priority: bool) -> Hazard | None:
        return self.admit_with_reason(state, candidate, priority=priority)[0]

    def admit_with_reason(
        self, state: SessionState, candidate: Hazard | None, *, priority: bool
    ) -> tuple[Hazard | None, str | None]:
        if candidate is None:
            return None, None
        now = int(time.monotonic() * 1000)
        cooldown_key = f"{candidate.kind}:{candidate.bearing}"
        self._prune_rate_window(state, now)
        exempt_from_global = (
            priority or candidate.severity == "S1" or candidate.level == "urgent"
        )
        if not priority:
            previous = state.last_alert_at_ms.get(cooldown_key)
            if previous is not None and now - previous < ALERT_DEBOUNCE_MS:
                return None, "debounce"
            if not exempt_from_global and len(state.alert_timestamps_ms) >= GLOBAL_RATE_LIMIT:
                return None, "global_rate"
        state.last_alert_at_ms[cooldown_key] = now
        state.alert_timestamps_ms.append(now)
        return candidate, None

    @staticmethod
    def _prune_rate_window(state: SessionState, now_ms: int) -> None:
        cutoff = now_ms - GLOBAL_RATE_WINDOW_MS
        state.alert_timestamps_ms[:] = [item for item in state.alert_timestamps_ms if item > cutoff]
