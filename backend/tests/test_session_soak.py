"""Long-session soak tests.

DEFAULT_LEASE_SECONDS is 180. These tests drive Redis admission across many lease periods of
virtual time. The simulator executes the Lua scripts' ZSET + owner-key semantics rather than
stubbing success.
"""

import time

import pytest

from akshrava_backend.session_admission import (
    ADMITTED,
    AT_CAPACITY,
    DEFAULT_LEASE_SECONDS,
    RedisSessionAdmission,
)


class SimulatedRedis:
    """Enough Redis to execute the device-keyed admission Lua scripts honestly."""

    def __init__(self):
        self.zset = {}
        self.owners = {}
        self.key_expires_at = None
        self.now = 1_700_000_000.0

    def advance(self, seconds):
        self.now += seconds
        if self.key_expires_at is not None and self.now >= self.key_expires_at:
            self.zset.clear()
            self.owners.clear()
            self.key_expires_at = None

    def _expire_members(self, now):
        for member in [m for m, score in self.zset.items() if score <= now]:
            del self.zset[member]
            self.owners.pop(member, None)

    async def eval(self, script, numkeys, zset_key, owner_key, *argv):
        if "DEL" in script and "ZREM" in script:
            session_id, device_id = argv[0], argv[1]
            if self.owners.get(device_id) == session_id:
                self.owners.pop(device_id, None)
                self.zset.pop(device_id, None)
            return 1
        session_id = argv[0]
        now = float(argv[1])
        lease_seconds = float(argv[2])
        maximum = int(argv[3])
        device_id = argv[4]
        self._expire_members(now)
        is_renew = "previous ~= session_id" in script or "previous and previous ~= session_id" in script
        previous = self.owners.get(device_id)
        if is_renew and "return 2" in script:
            if previous is not None and previous != session_id:
                return 2
            if previous is None and device_id not in self.zset and len(self.zset) >= maximum:
                return 0
        elif not is_renew:
            if device_id not in self.zset and len(self.zset) >= maximum:
                return 0
        displaced = previous is not None and previous != session_id
        self.owners[device_id] = session_id
        self.zset[device_id] = now + lease_seconds
        self.key_expires_at = self.now + lease_seconds
        if is_renew:
            return 1
        return 2 if displaced else 1

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
    monkeypatch.setattr(time, "time", lambda: fake.now)
    return admission, fake


@pytest.mark.asyncio
async def test_lease_lapses_without_renewal(monkeypatch):
    admission, fake = _admission(monkeypatch, maximum=1)
    assert await admission.try_open("s1", "tok1") == ADMITTED
    fake.advance(DEFAULT_LEASE_SECONDS + 1)
    assert await admission.try_open("s2", "tok2") == ADMITTED
    assert await admission.renew("s1", "tok1") == AT_CAPACITY


@pytest.mark.asyncio
async def test_streaming_session_survives_fifteen_minutes(monkeypatch):
    admission, fake = _admission(monkeypatch)
    assert await admission.try_open("walk", "tok") == ADMITTED
    evictions = []
    for tick in range(15 * 60):
        fake.advance(1.0)
        if await admission.renew("walk", "tok") != ADMITTED:
            evictions.append(tick)
    assert evictions == [], f"session evicted at t={evictions[:5]}s during a 15 minute walk"


@pytest.mark.asyncio
async def test_quiet_session_survives_fifteen_minutes(monkeypatch):
    admission, fake = _admission(monkeypatch)
    assert await admission.try_open("quiet", "tok") == ADMITTED
    evictions = []
    for minute in range(15):
        fake.advance(60.0)
        if await admission.renew("quiet", "tok") != ADMITTED:
            evictions.append(minute)
    assert evictions == [], f"quiet session evicted at minute {evictions[:5]}"


@pytest.mark.asyncio
async def test_session_recovers_when_a_lease_is_missed(monkeypatch):
    admission, fake = _admission(monkeypatch)
    assert await admission.try_open("stalled", "tok") == ADMITTED
    fake.advance(DEFAULT_LEASE_SECONDS + 30)
    assert await admission.renew("stalled", "tok") == ADMITTED
