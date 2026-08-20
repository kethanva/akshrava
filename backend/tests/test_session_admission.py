import time

import pytest

from akshrava_backend.session_admission import (
    ADMITTED,
    AT_CAPACITY,
    SUPERSEDED,
    InMemorySessionAdmission,
    RedisSessionAdmission,
    session_admission_for,
)


@pytest.mark.asyncio
async def test_session_admission_is_bounded_and_releases_capacity():
    admission = InMemorySessionAdmission(2)
    assert await admission.try_open("a", "sa") == ADMITTED
    assert await admission.try_open("b", "sb") == ADMITTED
    assert await admission.try_open("c", "sc") == AT_CAPACITY
    await admission.close("a", "sa")
    assert await admission.try_open("c", "sc") == ADMITTED


@pytest.mark.asyncio
async def test_second_connection_for_a_device_supersedes_the_first():
    admission = InMemorySessionAdmission(8)
    assert await admission.try_open("phone", "old") == ADMITTED
    assert await admission.try_open("phone", "new") == ADMITTED
    assert await admission.renew("phone", "old") == SUPERSEDED
    assert await admission.renew("phone", "new") == ADMITTED
    assert admission.active == 1


@pytest.mark.asyncio
async def test_superseded_session_close_does_not_evict_the_new_owner():
    admission = InMemorySessionAdmission(8)
    await admission.try_open("phone", "old")
    await admission.try_open("phone", "new")
    await admission.close("phone", "old")
    assert await admission.renew("phone", "new") == ADMITTED
    assert admission.active == 1


@pytest.mark.asyncio
async def test_reconnect_flap_consumes_one_admission_slot_not_many():
    admission = InMemorySessionAdmission(2)
    await admission.try_open("phone", "s1")
    await admission.try_open("phone", "s2")
    await admission.try_open("phone", "s3")
    assert admission.active == 1
    assert await admission.try_open("other", "x") == ADMITTED
    assert await admission.try_open("third", "y") == AT_CAPACITY


@pytest.mark.asyncio
async def test_quiet_session_reclaims_a_lapsed_lease_with_its_own_token():
    admission = InMemorySessionAdmission(1)
    await admission.try_open("phone", "live")
    await admission.close("phone", "live")
    assert await admission.renew("phone", "live") == ADMITTED


@pytest.mark.asyncio
async def test_capacity_exhaustion_still_reports_at_capacity():
    admission = InMemorySessionAdmission(1)
    await admission.try_open("a", "sa")
    assert await admission.try_open("b", "sb") == AT_CAPACITY
    assert await admission.renew("b", "sb") == AT_CAPACITY


def test_production_session_admission_requires_redis():
    with pytest.raises(ValueError, match="REDIS_URL"):
        session_admission_for(redis_url="", maximum=10, require_distributed=True)


@pytest.mark.asyncio
async def test_redis_session_admission_uses_atomic_shared_budget(monkeypatch):
    calls = []

    class FakeRedis:
        async def eval(self, script, numkeys, *argv):
            calls.append((script, numkeys) + argv)
            return 1

    admission = RedisSessionAdmission("redis://example.invalid/0", maximum=3)

    async def fake_client():
        return FakeRedis()

    monkeypatch.setattr(admission, "_client_for_use", fake_client)
    before = time.time()
    assert await admission.try_open("device-1", "session-1") == ADMITTED
    after = time.time()
    await admission.close("device-1", "session-1")

    script, numkeys, namespace, owner, session_id, now, lease_seconds, maximum, device_id = calls[0]
    assert "ZCARD" in script
    assert numkeys == 2
    assert namespace == "{akshrava:session-admission}"
    assert owner == "{akshrava:session-admission}:device:device-1"
    assert session_id == "session-1"
    assert device_id == "device-1"
    assert lease_seconds > 0
    assert maximum == 3
    assert before - 1 <= now <= after + 1
    assert now > 1_000_000_000
    close_script = calls[1][0]
    assert "GET" in close_script


@pytest.mark.asyncio
async def test_redis_session_admission_renew_refreshes_lease_only(monkeypatch):
    calls = []

    class FakeRedis:
        async def eval(self, script, numkeys, *argv):
            calls.append((script, numkeys) + argv)
            return 1

    admission = RedisSessionAdmission("redis://example.invalid/0", maximum=3)
    admission.lease_seconds = 180

    async def fake_client():
        return FakeRedis()

    monkeypatch.setattr(admission, "_client_for_use", fake_client)
    assert await admission.renew("device-alive", "session-alive") == ADMITTED
    script, numkeys, _namespace, _owner, session_id, now, lease_seconds, maximum, device_id = calls[0]
    assert "GET" in script
    assert numkeys == 2
    assert session_id == "session-alive"
    assert device_id == "device-alive"
    assert lease_seconds == 180
    assert maximum == 3
    assert now > 1_000_000_000


@pytest.mark.asyncio
async def test_inmemory_renew_requires_open_session_or_capacity():
    admission = InMemorySessionAdmission(2)
    assert admission.active == 0
    assert await admission.renew("missing", "token") == ADMITTED
    assert admission.active == 1
    assert await admission.try_open("a", "sa") == ADMITTED
    assert admission.active == 2
    assert await admission.renew("a", "sa") == ADMITTED
    await admission.close("nonexistent", "nope")
    assert admission.active == 2
    await admission.health()
    await admission.shutdown()


@pytest.mark.asyncio
async def test_redis_session_admission_health_and_shutdown(mock_redis_client):
    admission = RedisSessionAdmission("redis://localhost:6379/0", maximum=10)
    admission._client = mock_redis_client

    await admission.health()
    await admission.shutdown()


def test_session_admission_factory():
    adm = session_admission_for(redis_url="redis://localhost:6379/0", maximum=10, require_distributed=False)
    assert isinstance(adm, RedisSessionAdmission)
