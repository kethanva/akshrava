"""Application-layer alert delivery policy.

Hazard scoring is deliberately pure.  This policy owns the user/session-specific side effects
that decide whether an already-scored hazard is allowed to consume speech budget.
"""

import time
from typing import Optional

from .domain import Hazard, SessionState


ALERT_COOLDOWN_MS = 5_000
GLOBAL_RATE_LIMIT = 6
GLOBAL_RATE_WINDOW_MS = 60_000


class AlertPolicy:
    def admit(self, state: SessionState, candidate: Optional[Hazard], *, priority: bool) -> Optional[Hazard]:
        if candidate is None:
            return None
        now = int(time.monotonic() * 1000)
        cooldown_key = "%s:%s" % (candidate.kind, candidate.bearing)
        self._prune_rate_window(state, now)
        if not priority:
            previous = state.last_alert_at_ms.get(cooldown_key)
            if previous is not None and now - previous < ALERT_COOLDOWN_MS:
                return None
            if len(state.alert_timestamps_ms) >= GLOBAL_RATE_LIMIT:
                return None
        # A priority look intentionally answers once even if the ambient alert budget is spent.
        state.last_alert_at_ms[cooldown_key] = now
        state.alert_timestamps_ms.append(now)
        return candidate

    @staticmethod
    def _prune_rate_window(state: SessionState, now_ms: int) -> None:
        cutoff = now_ms - GLOBAL_RATE_WINDOW_MS
        state.alert_timestamps_ms[:] = [item for item in state.alert_timestamps_ms if item > cutoff]
