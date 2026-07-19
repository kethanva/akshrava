from akshrava_backend.rate_limit import FrameRateLimiter


def test_rate_limiter_enforces_burst_then_refills_without_transport_state():
    now = [0.0]
    limiter = FrameRateLimiter(1.0, 2.0, clock=lambda: now[0])
    assert limiter.allow()
    assert limiter.allow()
    assert not limiter.allow()
    now[0] = 1.0
    assert limiter.allow()
