"""Bounded process-local session admission.

WebSocket session state is intentionally ephemeral and local. A hard cap ensures a noisy fleet
cannot grow tracker maps or executor demand without limit; horizontal scale adds more bounded
replicas behind the edge.
"""

from threading import Lock


class SessionAdmission:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self._active = 0
        self._lock = Lock()

    def try_open(self) -> bool:
        with self._lock:
            if self._active >= self.maximum:
                return False
            self._active += 1
            return True

    def close(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def active(self) -> int:
        with self._lock:
            return self._active
