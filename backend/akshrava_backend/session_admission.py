"""Bounded session admission for WebSocket walking sessions.

Development keeps a process-local map. Production uses Redis so horizontally scaled API
replicas share one fleet-wide concurrent-session budget instead of each admitting up to the cap.

The unit of admission is ``device_id`` (one live walking socket per phone). ``session_id`` is a
fencing token so a reconnect can take over without a stale ``finally`` block deleting the winner.
"""

import asyncio
import time
from abc import ABC, abstractmethod

DEFAULT_LEASE_SECONDS = 180  # Short lease; clients renew via ping / frame traffic.

ADMITTED = "admitted"
AT_CAPACITY = "at_capacity"
SUPERSEDED = "superseded"


class SessionAdmission(ABC):
    @abstractmethod
    async def try_open(self, device_id: str, session_id: str) -> str:
        """Reserve capacity for this device. Newest session wins if the device is already live."""

    @abstractmethod
    async def renew(self, device_id: str, session_id: str) -> str:
        """Extend an active session lease. SUPERSEDED if another session owns the device."""

    @abstractmethod
    async def close(self, device_id: str, session_id: str) -> None:
        """Release capacity only if this session still owns the device."""

    @abstractmethod
    async def health(self) -> None:
        """Raise when the admission backend is unusable."""

    @abstractmethod
    async def shutdown(self) -> None:
        """Release client resources."""


class InMemorySessionAdmission(SessionAdmission):
    def __init__(self, maximum: int):
        self.maximum = maximum
        self._owners: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def try_open(self, device_id: str, session_id: str) -> str:
        async with self._lock:
            current = self._owners.get(device_id)
            if current is None and len(self._owners) >= self.maximum:
                return AT_CAPACITY
            self._owners[device_id] = session_id
            return ADMITTED

    async def renew(self, device_id: str, session_id: str) -> str:
        async with self._lock:
            current = self._owners.get(device_id)
            if current is not None and current != session_id:
                return SUPERSEDED
            if current is None:
                if len(self._owners) >= self.maximum:
                    return AT_CAPACITY
                self._owners[device_id] = session_id
                return ADMITTED
            return ADMITTED

    async def close(self, device_id: str, session_id: str) -> None:
        async with self._lock:
            if self._owners.get(device_id) == session_id:
                del self._owners[device_id]

    async def health(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @property
    def active(self) -> int:
        return len(self._owners)


class RedisSessionAdmission(SessionAdmission):
    """Atomic fleet-wide session cap: ZSET of device_id + STRING fencing token per device."""

    _OPEN_SCRIPT = """
local zset = KEYS[1]
local owner = KEYS[2]
local session_id = ARGV[1]
local now = tonumber(ARGV[2])
local lease = tonumber(ARGV[3])
local maximum = tonumber(ARGV[4])
local device_id = ARGV[5]
redis.call('ZREMRANGEBYSCORE', zset, '-inf', now)
local existing = redis.call('ZSCORE', zset, device_id)
if (not existing) and redis.call('ZCARD', zset) >= maximum then
  return 0
end
local previous = redis.call('GET', owner)
redis.call('SET', owner, session_id, 'EX', lease)
redis.call('ZADD', zset, now + lease, device_id)
redis.call('EXPIRE', zset, lease)
if previous and previous ~= session_id then
  return 2
end
return 1
"""

    _RENEW_SCRIPT = """
local zset = KEYS[1]
local owner = KEYS[2]
local session_id = ARGV[1]
local now = tonumber(ARGV[2])
local lease = tonumber(ARGV[3])
local maximum = tonumber(ARGV[4])
local device_id = ARGV[5]
redis.call('ZREMRANGEBYSCORE', zset, '-inf', now)
local previous = redis.call('GET', owner)
if previous and previous ~= session_id then
  return 2
end
if not previous then
  local existing = redis.call('ZSCORE', zset, device_id)
  if (not existing) and redis.call('ZCARD', zset) >= maximum then
    return 0
  end
end
redis.call('SET', owner, session_id, 'EX', lease)
redis.call('ZADD', zset, now + lease, device_id)
redis.call('EXPIRE', zset, lease)
return 1
"""

    _CLOSE_SCRIPT = """
local zset = KEYS[1]
local owner = KEYS[2]
local session_id = ARGV[1]
local device_id = ARGV[2]
if redis.call('GET', owner) == session_id then
  redis.call('DEL', owner)
  redis.call('ZREM', zset, device_id)
end
return 1
"""

    def __init__(self, url: str, maximum: int, namespace: str = "{akshrava:session-admission}"):
        self.url = url
        self.maximum = maximum
        self.namespace = namespace
        self.lease_seconds = DEFAULT_LEASE_SECONDS
        self._client = None

    def _owner_key(self, device_id: str) -> str:
        return f"{self.namespace}:device:{device_id}"

    async def _client_for_use(self):
        if self._client is None:
            from .redis_util import async_redis_from_url

            self._client = async_redis_from_url(
                self.url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1
            )
        return self._client

    def _map_open(self, result) -> str:
        if int(result) == 0:
            return AT_CAPACITY
        return ADMITTED

    def _map_renew(self, result) -> str:
        code = int(result)
        if code == 0:
            return AT_CAPACITY
        if code == 2:
            return SUPERSEDED
        return ADMITTED

    async def try_open(self, device_id: str, session_id: str) -> str:
        client = await self._client_for_use()
        now = time.time()
        result = await client.eval(
            self._OPEN_SCRIPT,
            2,
            self.namespace,
            self._owner_key(device_id),
            session_id,
            now,
            self.lease_seconds,
            self.maximum,
            device_id,
        )
        return self._map_open(result)

    async def renew(self, device_id: str, session_id: str) -> str:
        client = await self._client_for_use()
        now = time.time()
        result = await client.eval(
            self._RENEW_SCRIPT,
            2,
            self.namespace,
            self._owner_key(device_id),
            session_id,
            now,
            self.lease_seconds,
            self.maximum,
            device_id,
        )
        return self._map_renew(result)

    async def close(self, device_id: str, session_id: str) -> None:
        client = await self._client_for_use()
        await client.eval(
            self._CLOSE_SCRIPT,
            2,
            self.namespace,
            self._owner_key(device_id),
            session_id,
            device_id,
        )

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
        raise ValueError("REDIS_URL is required outside development for distributed session admission")
    return InMemorySessionAdmission(maximum)
