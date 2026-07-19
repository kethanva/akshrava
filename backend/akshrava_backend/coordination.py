"""Small distributed-coordination ports used by production runtime roles.

The application deliberately keeps the Redis dependency behind narrow interfaces.  That makes
the safety semantics testable while ensuring production cannot quietly fall back to per-process
state when a deployment adds API or GPU replicas.
"""

import asyncio
from abc import ABC, abstractmethod


class NonceStore(ABC):
    @abstractmethod
    async def claim(self, nonce: str, ttl_seconds: int) -> bool:
        """Atomically claim a nonce.  True means this is its first valid use."""

    @abstractmethod
    async def close(self) -> None:
        """Release any client resources."""

    @abstractmethod
    async def health(self) -> None:
        """Raise when the replay-protection backend is not usable."""


class InMemoryNonceStore(NonceStore):
    """Development/test implementation; unsafe across replicas by design."""

    def __init__(self):
        self._values = {}
        self._lock = asyncio.Lock()

    async def claim(self, nonce: str, ttl_seconds: int) -> bool:
        loop = asyncio.get_running_loop()
        now = loop.time()
        async with self._lock:
            self._values = {key: expires for key, expires in self._values.items() if expires > now}
            if nonce in self._values:
                return False
            self._values[nonce] = now + ttl_seconds
            return True

    async def close(self) -> None:
        return None

    async def health(self) -> None:
        return None


class RedisNonceStore(NonceStore):
    """Redis SET NX EX gives replay protection one shared, atomic decision point."""

    def __init__(self, url: str, namespace: str = "akshrava:worker-nonce"):
        self.url = url
        self.namespace = namespace
        self._client = None

    async def _client_for_use(self):
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self.url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        return self._client

    async def claim(self, nonce: str, ttl_seconds: int) -> bool:
        client = await self._client_for_use()
        result = await client.set(f"{self.namespace}:{nonce}", "1", ex=ttl_seconds, nx=True)
        return bool(result)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def health(self) -> None:
        client = await self._client_for_use()
        await client.ping()


def nonce_store_for(*, redis_url: str, require_distributed: bool) -> NonceStore:
    if redis_url:
        return RedisNonceStore(redis_url)
    if require_distributed:
        raise ValueError("NONCE_REDIS_URL is required for production GPU workers")
    return InMemoryNonceStore()


class DeviceRateLimiter(ABC):
    @abstractmethod
    async def allow(self, device_id: str, rate_per_second: float, burst: float) -> bool:
        """Consume one distributed token for a device, returning false when exhausted."""

    @abstractmethod
    async def close(self) -> None:
        """Release any client resources."""

    @abstractmethod
    async def health(self) -> None:
        """Raise when the coordination backend is not usable."""


class InMemoryDeviceRateLimiter(DeviceRateLimiter):
    def __init__(self):
        self._buckets = {}
        self._lock = asyncio.Lock()

    async def allow(self, device_id: str, rate_per_second: float, burst: float) -> bool:
        now = asyncio.get_running_loop().time()
        async with self._lock:
            tokens, seen = self._buckets.get(device_id, (burst, now))
            tokens = min(burst, tokens + (now - seen) * rate_per_second)
            self._buckets[device_id] = (tokens - 1, now) if tokens >= 1 else (tokens, now)
            return tokens >= 1

    async def close(self) -> None:
        return None

    async def health(self) -> None:
        return None


class RedisDeviceRateLimiter(DeviceRateLimiter):
    """Atomic token bucket shared by all API replicas and reconnects."""

    _SCRIPT = """
local state = redis.call('HMGET', KEYS[1], 'tokens', 'seen')
local tokens = tonumber(state[1]) or tonumber(ARGV[2])
local seen = tonumber(state[2]) or tonumber(ARGV[1])
local now = tonumber(ARGV[1])
local rate = tonumber(ARGV[3])
local burst = tonumber(ARGV[2])
tokens = math.min(burst, tokens + math.max(0, now - seen) * rate)
local allowed = tokens >= 1
if allowed then tokens = tokens - 1 end
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'seen', now)
redis.call('EXPIRE', KEYS[1], math.ceil((burst / rate) * 2))
return allowed and 1 or 0
"""

    def __init__(self, url: str, namespace: str = "akshrava:frame-rate"):
        self.url = url
        self.namespace = namespace
        self._client = None

    async def _client_for_use(self):
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self.url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        return self._client

    async def allow(self, device_id: str, rate_per_second: float, burst: float) -> bool:
        client = await self._client_for_use()
        now = asyncio.get_running_loop().time()
        result = await client.eval(self._SCRIPT, 1, f"{self.namespace}:{device_id}", now, burst, rate_per_second)
        return bool(result)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def health(self) -> None:
        client = await self._client_for_use()
        await client.ping()


def device_rate_limiter_for(*, redis_url: str, require_distributed: bool) -> DeviceRateLimiter:
    if redis_url:
        return RedisDeviceRateLimiter(redis_url)
    if require_distributed:
        raise ValueError("REDIS_URL is required for production distributed frame limits")
    return InMemoryDeviceRateLimiter()
