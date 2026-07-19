import pytest

from akshrava_backend.coordination import (
    InMemoryDeviceRateLimiter,
    InMemoryNonceStore,
    device_rate_limiter_for,
    nonce_store_for,
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
