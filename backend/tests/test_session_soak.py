"""Long-session soak tests.

Field reports say a walking session dies after roughly three minutes and has to be restarted
by hand. DEFAULT_LEASE_SECONDS is 180, so these tests drive the real admission and WebSocket
code across many lease periods of *virtual* time and assert the session survives a 15 minute
walk. The Redis behaviour is simulated faithfully (sorted set + score-based expiry) rather than
stubbed to always succeed, because the previous fake returned 1 unconditionally and therefore
could never have caught a lease that failed to renew.
"""

import time

import pytest

from akshrava_backend.session_admission import (
    DEFAULT_LEASE_SECONDS,
    RedisSessionAdmission,
)


class SimulatedRedis:
    """Enough Redis to execute the two admission Lua scripts honestly.

    Implements the sorted set operations the scripts actually use, including
    ZREMRANGEBYSCORE-based lease expiry, so a lease that is never renewed really does lapse.
    """

    def __init__(self):
        self.zset = {}  # member -> score (lease expiry, epoch seconds)
        self.key_expires_at = None
        self.now = 1_700_000_000.0

    def advance(self, seconds):
        self.now += seconds
        # Whole-key TTL, as set by EXPIRE in the scripts.
        if self.key_expires_at is not None and self.now >= self.key_expires_at:
            self.zset.clear()
            self.key_expires_at = None

    async def eval(self, script, numkeys, key, session_id, now, lease_seconds, maximum=None):
        now = float(now)
        lease_seconds = float(lease_seconds)
        # ZREMRANGEBYSCORE key -inf now
        for member in [m for m, score in self.zset.items() if score <= now]:
            del self.zset[member]

        is_open_script = "ZCARD" in script
        if session_id in self.zset:
            self.zset[session_id] = now + lease_seconds
            self.key_expires_at = self.now + lease_seconds
            return 1
        if not is_open_script:
            return 0  # renew on a lapsed lease
        if maximum is not None and len(self.zset) >= int(maximum):
            return 0
        self.zset[session_id] = now + lease_seconds
        self.key_expires_at = self.now + lease_seconds
        return 1

    async def zrem(self, key, session_id):
        self.zset.pop(session_id, None)

    async def ping(self):
        return True

    async def aclose(self):
        return None


def _admission(monkeypatch, maximum=200):
    fake = SimulatedRedis()
    admission = RedisSessionAdmission("redis://example.invalid/0", maximum=maximum)

    async def client_for_use():
        return fake

    monkeypatch.setattr(admission, "_client_for_use", client_for_use)
    # The production code calls time.time(); drive it from the simulator's clock.
    monkeypatch.setattr(time, "time", lambda: fake.now)
    return admission, fake


@pytest.mark.asyncio
async def test_lease_lapses_without_renewal(monkeypatch):
    """Guard rail: prove the simulator can actually fail, or the soak below proves nothing."""
    admission, fake = _admission(monkeypatch)
    assert await admission.try_open("s1")
    fake.advance(DEFAULT_LEASE_SECONDS + 1)
    assert not await admission.renew("s1"), "an un-renewed lease must lapse"


@pytest.mark.asyncio
async def test_streaming_session_survives_fifteen_minutes(monkeypatch):
    """A session renewing at the phone's frame cadence must never be evicted."""
    admission, fake = _admission(monkeypatch)
    assert await admission.try_open("walk")

    # 15 minutes at ~1.2 FPS, renewing on each frame as main.py does.
    evictions = []
    for tick in range(15 * 60):
        fake.advance(1.0)
        if not await admission.renew("walk"):
            evictions.append(tick)
    assert evictions == [], f"session evicted at t={evictions[:5]}s during a 15 minute walk"


@pytest.mark.asyncio
async def test_quiet_session_survives_fifteen_minutes(monkeypatch):
    """A stationary user sends no frames; only the 60 s app-level ping renews the lease.

    This is the case that actually matters: duplicate frames are dropped on the phone, so a
    user standing still legitimately produces no traffic except ProtocolClient's ping.
    """
    admission, fake = _admission(monkeypatch)
    assert await admission.try_open("quiet")

    evictions = []
    for minute in range(15):
        fake.advance(60.0)  # APP_PING_INTERVAL_MS
        if not await admission.renew("quiet"):
            evictions.append(minute)
    assert evictions == [], f"quiet session evicted at minute {evictions[:5]}"


@pytest.mark.asyncio
async def test_session_recovers_when_a_lease_is_missed(monkeypatch):
    """A lapsed lease on a live socket must re-admit, not kill the walk.

    main._renew_or_readmit falls back to try_open precisely so a network stall longer than the
    lease does not end a session whose socket is still open.
    """
    admission, fake = _admission(monkeypatch)
    assert await admission.try_open("stalled")
    fake.advance(DEFAULT_LEASE_SECONDS + 30)

    assert not await admission.renew("stalled")
    assert await admission.try_open("stalled"), "a live socket must be re-admitted after a stall"
