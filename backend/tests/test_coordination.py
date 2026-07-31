import time

import pytest

from akshrava_backend.coordination import (
    InMemoryDeviceRateLimiter,
    InMemoryNonceStore,
    RedisDeviceRateLimiter,
    RedisNonceStore,
    device_rate_limiter_for,
    nonce_store_for,
    use_redis_frame_limiter,
)


@pytest.mark.asyncio
async def test_in_memory_nonce_store_claims_once_until_expiry():
    store = InMemoryNonceStore()
    assert await store.claim("a", 60)
    assert not await store.claim("a", 60)


def test_production_requires_distributed_nonce_store():
    with pytest.raises(ValueError, match="NONCE_REDIS_URL"):
        nonce_store_for(redis_url="", require_distributed=True)


@pytest.mark.asyncio
async def test_in_memory_device_rate_limiter_enforces_burst():
    limiter = InMemoryDeviceRateLimiter()
    assert await limiter.allow("phone-a", 1.0, 2.0)
    assert await limiter.allow("phone-a", 1.0, 2.0)
    assert not await limiter.allow("phone-a", 1.0, 2.0)


def test_production_requires_distributed_frame_rate_limiter():
    with pytest.raises(ValueError, match="REDIS_URL"):
        device_rate_limiter_for(redis_url="", require_distributed=True)


def test_use_redis_frame_limiter_whenever_redis_url_is_set():
    assert use_redis_frame_limiter(redis_url="redis://localhost:6379/0") is True
    assert use_redis_frame_limiter(redis_url="rediss://memorystore:6378/0") is True
    assert use_redis_frame_limiter(redis_url="") is False
    assert use_redis_frame_limiter(redis_url="   ") is False


@pytest.mark.asyncio
async def test_redis_device_rate_limiter_passes_wall_clock_epoch(monkeypatch):
    calls = []

    class FakeRedis:
        async def eval(self, script, keys, key, now, burst, rate):
            calls.append((key, now, burst, rate))
            return 1

    limiter = RedisDeviceRateLimiter("redis://example.invalid/0")

    async def fake_client():
        return FakeRedis()

    monkeypatch.setattr(limiter, "_client_for_use", fake_client)
    before = time.time()
    assert await limiter.allow("phone-a", 1.2, 2.0)
    after = time.time()
    key, now, burst, rate = calls[0]
    assert key.endswith("phone-a")
    assert burst == 2.0 and rate == 1.2
    assert before - 1 <= now <= after + 1
    assert now > 1_000_000_000


@pytest.mark.asyncio
async def test_redis_nonce_store(mock_redis_client):
    from akshrava_backend.coordination import RedisNonceStore

    store = RedisNonceStore("redis://localhost:6379/0")
    store._client = mock_redis_client

    assert await store.claim("nonce123", 30) is True
    # mock_redis_client returns True on first set
    await store.health()
    await store.close()


@pytest.mark.asyncio
async def test_redis_device_rate_limiter_health_and_close(mock_redis_client):
    from akshrava_backend.coordination import RedisDeviceRateLimiter

    limiter = RedisDeviceRateLimiter("redis://localhost:6379/0")
    limiter._client = mock_redis_client

    await limiter.health()
    await limiter.close()


@pytest.mark.asyncio
async def test_in_memory_stores_health_and_close():
    store = InMemoryNonceStore()
    await store.health()
    await store.close()

    limiter = InMemoryDeviceRateLimiter()
    await limiter.health()
    await limiter.close()


def test_coordination_factories():
    ns = nonce_store_for(redis_url="redis://localhost:6379/0", require_distributed=False)
    assert isinstance(ns, RedisNonceStore)

    rl = device_rate_limiter_for(redis_url="redis://localhost:6379/0", require_distributed=False)
    assert isinstance(rl, RedisDeviceRateLimiter)

