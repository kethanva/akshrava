"""Transport-agnostic token-bucket policy for one phone session."""

import time


class FrameRateLimiter:
    """Bound a device's offered frame rate without retaining frame contents."""

    def __init__(self, rate_per_second: float, burst: float, clock=time.monotonic):
        self.rate_per_second = rate_per_second
        self.burst = burst
        self.clock = clock
        self.tokens = burst
        self.last = clock()

    def allow(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate_per_second)
        self.last = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True
