import os
from datetime import datetime, timedelta, timezone

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import DateTime, text

from akshrava_backend.storage import AlertEvent, Base, CalibrationProfileRecord, Device, Store


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
                    f"{model.__tablename__}.{column.name} must be DateTime(timezone=True)"
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
        assert profile.reference_height_px == 480
        await store.upsert_calibration_profile(
            "pilot-r0", 520.0, 1.4, verified=True, reference_height_px=240
        )
        profile = await store.geometry_profile("pilot-r0")
        assert profile is not None
        assert profile.reference_height_px == 240
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
        # Mirrors the real redis-py asyncio client, which deprecated close() in 5.0.1.
        # A fake that still exposes only close() would hide a caller left on the alias.
        async def aclose(self):
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

        # Mirrors the real redis-py asyncio client, which deprecated close() in 5.0.1.
        # A fake that still exposes only close() would hide a caller left on the alias.
        async def aclose(self):
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


@pytest.mark.asyncio
async def test_revocation_cache_is_lru_bounded(tmp_path, monkeypatch):
    # A long-lived API process must not grow the in-memory revocation cache without bound as
    # rotated device IDs churn through it. Cap it and LRU-evict the oldest entries.
    import akshrava_backend.storage as storage_mod

    monkeypatch.setattr(storage_mod, "_REVOCATION_CACHE_MAX", 5)
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "lru.db"))
    await store.initialize()
    try:
        for index in range(20):
            await store.is_device_revoked("device-%d" % index)
        assert len(store._revocation_cache) <= 5
        # The most recently queried ids survive; the oldest were evicted.
        assert "device-19" in store._revocation_cache
        assert "device-0" not in store._revocation_cache
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_leader_purge_on_sqlite_runs_directly_without_an_advisory_lock(tmp_path):
    """SQLite is single-process dev/test: there is no fleet to coordinate and no lock support."""
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "leader-sqlite.db"))
    await store.initialize()
    try:
        async with store.sessions() as session:
            session.add(
                AlertEvent(
                    device_id="dev-1", frame_id=1, kind="vehicle", level="caution",
                    bearing="ahead", confidence=0.9,
                    created_at=datetime.now(timezone.utc) - timedelta(days=60),
                )
            )
            await session.commit()

        assert await store.purge_alert_events_if_leader(30) == 1
        # Idempotent: nothing left to remove on the next cycle.
        assert await store.purge_alert_events_if_leader(30) == 0
    finally:
        await store.close()


def _postgres_store_with_fake_connection(execute_side_effect=None, lock_acquired=True):
    """A Postgres-URL Store whose engine hands out one mock connection.

    AsyncEngine.connect is read-only, so the engine object itself is replaced rather than patched.
    """
    store = Store("postgresql+asyncpg://user:pw@localhost:5432/akshrava")
    connection = AsyncMock()
    connection.execution_options = AsyncMock(return_value=connection)
    if execute_side_effect is not None:
        connection.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        lock_result = MagicMock()
        lock_result.scalar.return_value = lock_acquired
        connection.execute = AsyncMock(return_value=lock_result)
    fake_engine = MagicMock()
    fake_engine.connect = AsyncMock(return_value=connection)
    store.engine = fake_engine
    return store, connection


@pytest.mark.asyncio
async def test_leader_purge_skips_work_when_another_instance_holds_the_lock():
    """A lost advisory lock is a normal outcome, not an error.

    Ten Cloud Run instances each run their own retention loop. Exactly one should delete; the
    rest must return None immediately and wait for their next interval rather than piling into
    the same batched DELETE on alert_events while phones are streaming.
    """
    # lock_acquired=False -> somebody else is already purging.
    store, connection = _postgres_store_with_fake_connection(lock_acquired=False)

    with patch.object(store, "purge_alert_events_older_than", AsyncMock()) as raw_purge:
        assert await store.purge_alert_events_if_leader(30) is None

    raw_purge.assert_not_called()
    connection.close.assert_awaited()


@pytest.mark.asyncio
async def test_leader_purge_always_releases_the_session_lock_even_when_the_purge_fails():
    """The advisory lock is session-scoped and the connection returns to the pool, not closed.

    A leaked lock would silently disable retention for the entire life of the process, so the
    unlock must run on the failure path too.
    """
    statements: list[str] = []

    async def record(statement, *args, **kwargs):
        statements.append(str(statement))
        result = MagicMock()
        result.scalar.return_value = True  # lock acquired
        return result

    store, connection = _postgres_store_with_fake_connection(execute_side_effect=record)

    with patch.object(
        store, "purge_alert_events_older_than", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        with pytest.raises(RuntimeError):
            await store.purge_alert_events_if_leader(30)

    assert any("pg_try_advisory_lock" in item for item in statements)
    assert any("pg_advisory_unlock" in item for item in statements), (
        "session-level advisory lock must be released even when the purge raises"
    )
    connection.close.assert_awaited()


@pytest.mark.asyncio
async def test_leader_purge_reports_the_purge_result_even_if_unlock_fails():
    """Failing to release the lock must never mask a successful purge."""
    async def flaky(statement, *args, **kwargs):
        if "pg_advisory_unlock" in str(statement):
            raise RuntimeError("unlock failed")
        result = MagicMock()
        result.scalar.return_value = True
        return result

    store, connection = _postgres_store_with_fake_connection(execute_side_effect=flaky)

    with patch.object(store, "purge_alert_events_older_than", AsyncMock(return_value=7)):
        assert await store.purge_alert_events_if_leader(30) == 7
    connection.invalidate.assert_awaited_once()
    connection.close.assert_awaited_once()


_POSTGRES_URL = os.getenv("DATABASE_URL", "")
_HAS_POSTGRES = _POSTGRES_URL.startswith("postgresql")


@pytest.mark.skipif(not _HAS_POSTGRES, reason="requires a real Postgres DATABASE_URL (CI provides one)")
@pytest.mark.asyncio
async def test_leader_election_against_real_postgres_advisory_locks():
    """Execute the advisory-lock SQL for real, not against a mock.

    Every other leader-election test stubs the connection, so a typo in the lock SQL, a wrong
    parameter style, or a function that does not exist would pass all of them and only fail in
    production -- where the symptom is retention silently never running. CI runs a Postgres
    service, so exercise the real primitive there.
    """
    from akshrava_backend.storage import _RETENTION_ADVISORY_LOCK_KEY

    leader = Store(_POSTGRES_URL, bootstrap_schema=False)
    contender = Store(_POSTGRES_URL, bootstrap_schema=False)
    try:
        # Uncontended: the lock is taken, the purge runs, and a row count comes back.
        first = await leader.purge_alert_events_if_leader(30)
        assert isinstance(first, int)

        # Hold the lock on an independent session and confirm a second instance stands down
        # instead of piling into the same batched DELETE.
        holder = await contender.engine.connect()
        holder = await holder.execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = (
                await holder.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": _RETENTION_ADVISORY_LOCK_KEY},
                )
            ).scalar()
            assert acquired is True, "test could not take the lock it needs to contend for"

            assert await leader.purge_alert_events_if_leader(30) is None
        finally:
            await holder.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _RETENTION_ADVISORY_LOCK_KEY}
            )
            await holder.close()

        # Lock released: the leader can purge again. This also proves the earlier successful
        # purge released its own session-level lock rather than leaking it onto a pooled
        # connection, which would disable retention for the life of the process.
        assert isinstance(await leader.purge_alert_events_if_leader(30), int)
    finally:
        await leader.close()
        await contender.close()


def test_non_sqlite_engine_uses_a_bounded_connection_pool():
    # Cloud Run autoscaling + SQLAlchemy's default 15-conn pool would exhaust a 1-vCPU Cloud SQL
    # instance. The Postgres engine must use a small, explicitly bounded pool with pre_ping.
    store = Store("postgresql+asyncpg://user:pw@localhost:5432/akshrava")
    pool = store.engine.pool
    assert pool.size() == storage_mod_pool_size()
    # pre_ping is on so Cloud SQL dropping idle connections does not fail the next query.
    assert store.engine.pool._pre_ping is True


def storage_mod_pool_size():
    import akshrava_backend.storage as storage_mod
    return storage_mod._POOL_SIZE


@pytest.mark.asyncio
async def test_storage_additional_coverage_paths(tmp_path):
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "extra.db"), redis_url="redis://localhost:6379/0")
    await store.initialize()
    try:
        # pool_status
        checked_in, checked_out = store.pool_status()
        assert checked_in >= 0 and checked_out >= 0

        # ping
        await store.ping()

        # record_alert
        from akshrava_backend.domain import Hazard
        hazard = Hazard(
            kind="person",
            level="caution",
            bearing="ahead",
            message_key="person_ahead",
            haptic="center",
            confidence=0.88,
            severity="medium",
            range_band="medium",
            track_id=1,
        )
        await store.record_alert("device-1", 10, hazard)
        events = await store.recent_events("device-1", limit=10)
        assert len(events) == 1
        assert events[0].kind == "person"

        # upsert_calibration_profile invalid reference height
        with pytest.raises(ValueError, match="reference_height_px must be positive"):
            await store.upsert_calibration_profile("c1", 500.0, 1.2, reference_height_px=0)

        # _get_redis_client instantiation
        mock_redis = AsyncMock()
        with patch("akshrava_backend.redis_util.async_redis_from_url", return_value=mock_redis) as mock_from_url:
            store._redis_client = None
            client = await store._get_redis_client()
            assert client is not None
            mock_from_url.assert_called_once()

    finally:
        await store.close()


@pytest.mark.asyncio
async def test_storage_schema_verification_errors(tmp_path):
    store = Store("sqlite+aiosqlite:///%s" % (tmp_path / "schema_err.db"))
    # Don't bootstrap schema, so missing tables error triggers
    with pytest.raises(RuntimeError, match="database schema is not migrated"):
        await store.verify_schema()

    # Create tables but expect invalid revision
    async with store.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        await conn.execute(text("INSERT INTO alembic_version VALUES ('20200101_01')"))

    store.expected_schema_revision = "20260721_01"
    with pytest.raises(RuntimeError, match="revision mismatch: expected 20260721_01, found 20200101_01"):
        await store.verify_schema()
