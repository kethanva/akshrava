from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import DateTime, text

from akshrava_backend.storage import AlertEvent, CalibrationProfileRecord, Device, Store


def test_every_timestamp_column_is_declared_timezone_aware():
    # asyncpg raises DataError writing a tz-aware Python datetime into a naive TIMESTAMP
    # column. Every model in this codebase uses datetime.now(timezone.utc) as its default,
    # so every DateTime column MUST be declared timezone=True or the very first Postgres
    # write in production (the documented docker-compose deployment) fails. SQLite ignores
    # the distinction, so this regression is invisible to every SQLite-backed test above —
    # assert against the mapped column type directly instead.
    for model in (Device, AlertEvent):
        for column in model.__table__.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, (
                    "%s.%s must be DateTime(timezone=True)" % (model.__tablename__, column.name)
                )


@pytest.mark.asyncio
async def test_alert_retention_purges_only_expired_alert_events(tmp_path):
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "retention.db"))
    await store.initialize()
    try:
        async with store.sessions() as session:
            session.add_all(
                [
                    AlertEvent(
                        device_id="old-device", frame_id=1, kind="obstacle", level="caution",
                        bearing="ahead", confidence=0.8,
                        created_at=datetime.now(timezone.utc) - timedelta(days=31),
                    ),
                    AlertEvent(
                        device_id="fresh-device", frame_id=2, kind="obstacle", level="caution",
                        bearing="ahead", confidence=0.8,
                        created_at=datetime.now(timezone.utc) - timedelta(days=1),
                    ),
                ]
            )
            await session.commit()

        assert await store.purge_alert_events_older_than(30) == 1
        assert len(await store.recent_events("old-device")) == 0
        assert len(await store.recent_events("fresh-device")) == 1
    finally:
        await store.engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_store_enables_wal_for_concurrent_pilot_writes(tmp_path):
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "wal.db"))
    await store.initialize()
    try:
        async with store.engine.connect() as connection:
            assert (await connection.execute(text("PRAGMA journal_mode"))).scalar().lower() == "wal"
    finally:
        await store.engine.dispose()


@pytest.mark.asyncio
async def test_geometry_profile_is_unavailable_until_explicitly_verified(tmp_path):
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "profiles.db"))
    await store.initialize()
    try:
        async with store.sessions() as session:
            session.add(CalibrationProfileRecord(calibration_id="r0", focal_px=500.0, camera_height_m=1.35))
            await session.commit()
        assert await store.geometry_profile("r0") is None
        async with store.sessions() as session:
            record = await session.get(CalibrationProfileRecord, "r0")
            record.verified = True
            await session.commit()
        profile = await store.geometry_profile("r0")
        assert profile is not None
        assert profile.calibration_id == "r0"
    finally:
        await store.engine.dispose()


@pytest.mark.asyncio
async def test_upsert_calibration_profile_requires_verified_flag_for_geometry(tmp_path):
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "upsert-profile.db"))
    await store.initialize()
    try:
        await store.upsert_calibration_profile("pilot-r0", 520.0, 1.4, verified=False)
        assert await store.geometry_profile("pilot-r0") is None
        await store.upsert_calibration_profile("pilot-r0", 520.0, 1.4, verified=True)
        profile = await store.geometry_profile("pilot-r0")
        assert profile is not None
        assert profile.focal_px == 520.0
        assert profile.camera_height_m == 1.4
    finally:
        await store.engine.dispose()


@pytest.mark.asyncio
async def test_revoked_device_is_denied_by_the_connection_check(tmp_path):
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "revocation.db"))
    await store.initialize()
    try:
        await store.upsert_device("pilot-phone-1", "r0")
        assert not await store.is_device_revoked("pilot-phone-1")
        assert await store.revoke_device("pilot-phone-1")
        assert await store.is_device_revoked("pilot-phone-1")
        assert not await store.revoke_device("missing-device")
    finally:
        await store.engine.dispose()


@pytest.mark.asyncio
async def test_production_store_requires_the_expected_alembic_revision(tmp_path):
    store = Store(
        "sqlite+aiosqlite:///%s" % (tmp_path / "revision.db"),
        bootstrap_schema=True,
        expected_schema_revision="20260719_01",
    )
    with pytest.raises(RuntimeError, match="revision mismatch"):
        await store.initialize()
    await store.engine.dispose()


@pytest.mark.asyncio
async def test_device_revocation_uses_redis_cache():
    class MockRedis:
        def __init__(self):
            self.data = {}
        async def get(self, key):
            return self.data.get(key)
        async def set(self, key, value, ex=None):
            self.data[key] = value
        async def delete(self, key):
            self.data.pop(key, None)
        async def close(self):
            pass

    mock_client = MockRedis()
    store = Store("sqlite+aiosqlite:///:memory:", redis_url="redis://localhost:6379")
    store._redis_client = mock_client
    await store.initialize()
    try:
        await store.upsert_device("test-device-redis", "r0")
        assert not await store.is_device_revoked("test-device-redis")
        assert mock_client.data.get("revocation:test-device-redis") == b"0"
        mock_client.data["revocation:test-device-redis"] = b"1"
        assert await store.is_device_revoked("test-device-redis")
        assert await store.revoke_device("test-device-redis")
        assert mock_client.data.get("revocation:test-device-redis") == b"1"
        assert await store.is_device_revoked("test-device-redis")
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_revocation_publishes_short_ttl_negative_and_revoke_overwrites():
    class MockRedis:
        def __init__(self):
            self.data = {}
            self.expiry = {}

        async def get(self, key):
            return self.data.get(key)

        async def set(self, key, value, ex=None):
            self.data[key] = value
            self.expiry[key] = ex

        async def delete(self, key):
            self.data.pop(key, None)

        async def close(self):
            pass

    mock_client = MockRedis()
    store = Store("sqlite+aiosqlite:///:memory:", redis_url="redis://localhost:6379")
    store._redis_client = mock_client
    await store.initialize()
    try:
        await store.upsert_device("active-device", "r0")
        assert not await store.is_device_revoked("active-device")
        assert mock_client.data.get("revocation:active-device") == b"0"
        assert mock_client.expiry.get("revocation:active-device") == 5
        # Second call must not hit DB again while local/redis negative is fresh.
        assert not await store.is_device_revoked("active-device")
        assert await store.revoke_device("active-device")
        assert mock_client.data.get("revocation:active-device") == b"1"
        assert await store.is_device_revoked("active-device")
    finally:
        await store.close()
