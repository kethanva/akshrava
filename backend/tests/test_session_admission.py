import pytest

from akshrava_backend.session_admission import (
    InMemorySessionAdmission,
    RedisSessionAdmission,
    session_admission_for,
)


@pytest.mark.asyncio
async def test_session_admission_is_bounded_and_releases_capacity():
    admission = InMemorySessionAdmission(2)
    assert await admission.try_open("a")
    assert await admission.try_open("b")
    assert not await admission.try_open("c")
    await admission.close("a")
    assert await admission.try_open("c")


def test_production_session_admission_requires_redis():
    with pytest.raises(ValueError, match="REDIS_URL"):
        session_admission_for(redis_url="", maximum=10, require_distributed=True)


@pytest.mark.asyncio
async def test_redis_session_admission_uses_atomic_shared_budget(monkeypatch):
    calls = []

    class FakeRedis:
        async def eval(self, script, keys, namespace, session_id, now, lease_seconds, maximum):
            calls.append((script, keys, namespace, session_id, lease_seconds, maximum))
            return 1

        async def zrem(self, namespace, session_id):
            calls.append(("zrem", namespace, session_id))

    admission = RedisSessionAdmission("redis://example.invalid/0", maximum=3)

    async def fake_client():
        return FakeRedis()

    monkeypatch.setattr(admission, "_client_for_use", fake_client)
    assert await admission.try_open("session-1")
    await admission.close("session-1")

    script, keys, namespace, session_id, lease_seconds, maximum = calls[0]
    assert "ZCARD" in script
    assert keys == 1
    assert namespace == "akshrava:session-admission"
    assert session_id == "session-1"
    assert lease_seconds > 0
    assert maximum == 3
    assert calls[1] == ("zrem", "akshrava:session-admission", "session-1")
