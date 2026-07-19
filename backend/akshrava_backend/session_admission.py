"""Bounded session admission for WebSocket walking sessions.

Development keeps a process-local counter. Production uses Redis so horizontally scaled API
replicas share one fleet-wide concurrent-session budget instead of each admitting up to the cap.
"""

import asyncio
import time
from abc import ABC, abstractmethod


DEFAULT_LEASE_SECONDS = 12 * 60 * 60


class SessionAdmission(ABC):
    @abstractmethod
    async def try_open(self, session_id: str) -> bool:
        """Reserve capacity for a session id."""

    @abstractmethod
    async def close(self, session_id: str) -> None:
        """Release a previously reserved session id."""

    @abstractmethod
    async def health(self) -> None:
        """Raise when the admission backend is unusable."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release client resources."""


class InMemorySessionAdmission(SessionAdmission):
    def __init__(self, maximum: int):
        self.maximum = maximum
        self._active = 0
        self._sessions = set()
        self._lock = asyncio.Lock()

    async def try_open(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._sessions:
                return True
            if self._active >= self.maximum:
                return False
            self._active += 1
            self._sessions.add(session_id)
            return True

    async def close(self, session_id: str) -> None:
        async with self._lock:
            if session_id not in self._sessions:
                return
            self._sessions.remove(session_id)
            self._active = max(0, self._active - 1)

    async def health(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @property
    def active(self) -> int:
        return self._active


class RedisSessionAdmission(SessionAdmission):
    """Atomic fleet-wide session cap backed by a Redis sorted set."""

    _SCRIPT = """
local key = KEYS[1]
local session_id = ARGV[1]
local now = tonumber(ARGV[2])
local lease_seconds = tonumber(ARGV[3])
local maximum = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZSCORE', key, session_id) then
  redis.call('ZADD', key, now + lease_seconds, session_id)
  redis.call('EXPIRE', key, lease_seconds)
  return 1
end
if redis.call('ZCARD', key) >= maximum then
  return 0
end
redis.call('ZADD', key, now + lease_seconds, session_id)
redis.call('EXPIRE', key, lease_seconds)
return 1
"""

    def __init__(self, url: str, maximum: int, namespace: str = "akshrava:session-admission"):
        self.url = url
        self.maximum = maximum
        self.namespace = namespace
        self.lease_seconds = DEFAULT_LEASE_SECONDS
        self._client = None

    async def _client_for_use(self):
        if self._client is None:
            from redis.asyncio import Redis

            self._client = Redis.from_url(self.url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        return self._client

    async def try_open(self, session_id: str) -> bool:
        client = await self._client_for_use()
        # Redis leases must use wall-clock epoch seconds shared across replicas. asyncio loop
        # time is monotonic and process-local; mixing it into Redis would desync expiry.
        now = time.time()
        result = await client.eval(self._SCRIPT, 1, self.namespace, session_id, now, self.lease_seconds, self.maximum)
        return bool(result)

    async def close(self, session_id: str) -> None:
        client = await self._client_for_use()
        await client.zrem(self.namespace, session_id)

    async def health(self) -> None:
        client = await self._client_for_use()
        await client.ping()

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()


def session_admission_for(*, redis_url: str, maximum: int, require_distributed: bool) -> SessionAdmission:
    if redis_url:
        return RedisSessionAdmission(redis_url, maximum)
    if require_distributed:
        raise ValueError("REDIS_URL is required for production distributed session admission")
    return InMemorySessionAdmission(maximum)
