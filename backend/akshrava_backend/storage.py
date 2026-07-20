from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, delete, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .domain import GeometryProfile

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# All timestamp columns are timezone-aware. asyncpg rejects tz-aware Python datetimes against a
# naive TIMESTAMP column (DataError), so a bare DateTime() here would pass every SQLite-backed
# test and then fail on the very first Postgres write in the documented docker-compose path.
class Device(Base):
    __tablename__ = "devices"
    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    calibration_id: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CalibrationProfileRecord(Base):
    """Provisioned geometry only; no image, route, or person data is stored here."""

    __tablename__ = "calibration_profiles"
    calibration_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    focal_px: Mapped[float] = mapped_column(Float)
    camera_height_m: Mapped[float] = mapped_column(Float)
    verified: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    frame_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(32))
    bearing: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(8), nullable=True)
    range_band: Mapped[str] = mapped_column(String(16), nullable=True)
    message_key: Mapped[str] = mapped_column(String(64), nullable=True)
    track_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class Store:
    def __init__(
        self,
        url,
        *,
        redis_url: Optional[str] = None,
        bootstrap_schema: bool = True,
        expected_schema_revision: Optional[str] = None,
    ):
        self.engine = create_async_engine(url, future=True)
        self.bootstrap_schema = bootstrap_schema
        self.redis_url = redis_url
        self._redis_client = None
        self._revocation_cache = {}  # device_id -> (revoked_bool, expiry_timestamp)
        self._cache_ttl = 15.0
        # Short negative TTL: avoid Postgres on every frame while keeping revoke lag ≤ this window.
        self._negative_cache_ttl = 5.0
        self.expected_schema_revision = expected_schema_revision
        if url.startswith("sqlite"):
            @event.listens_for(self.engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def _get_redis_client(self):
        if self._redis_client is None and self.redis_url:
            from .redis_util import async_redis_from_url

            self._redis_client = async_redis_from_url(self.redis_url)
        return self._redis_client

    async def close(self):
        if self._redis_client is not None:
            await self._redis_client.close()
            self._redis_client = None
        await self.engine.dispose()

    async def initialize(self):
        """Initialize only disposable development/test schemas.

        Production schema changes are executed by Alembic before application rollout.  Running
        DDL from every API process creates a deployment race and makes rollback impossible.
        """
        if self.bootstrap_schema:
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        await self.verify_schema()

    async def verify_schema(self):
        required = {"devices", "calibration_profiles", "alert_events"}
        async with self.engine.connect() as connection:
            present = await connection.run_sync(
                lambda sync_connection: set(inspect(sync_connection).get_table_names())
            )
        missing = required - present
        if missing:
            raise RuntimeError("database schema is not migrated; missing tables: %s" % ", ".join(sorted(missing)))
        if self.expected_schema_revision:
            if "alembic_version" not in present:
                raise RuntimeError(
                    "database schema revision mismatch: expected %s, found none" % self.expected_schema_revision
                )
            async with self.engine.connect() as connection:
                revision = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
            if revision != self.expected_schema_revision:
                raise RuntimeError(
                    "database schema revision mismatch: expected %s, found %s"
                    % (self.expected_schema_revision, revision or "none")
                )

    async def upsert_device(self, device_id, calibration_id):
        # Two sockets for the same device can race here across a reconnect (old socket's
        # teardown overlapping the new one's first frame). get-then-insert is not atomic, so
        # fall back to an update on the duplicate-key error rather than losing the session.
        async with self.sessions() as session:
            device = await session.get(Device, device_id)
            if device is None:
                session.add(Device(device_id=device_id, calibration_id=calibration_id))
            else:
                device.calibration_id = calibration_id
                device.updated_at = datetime.now(timezone.utc)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                async with self.sessions() as retry_session:
                    device = await retry_session.get(Device, device_id)
                    if device is not None:
                        device.calibration_id = calibration_id
                        device.updated_at = datetime.now(timezone.utc)
                        await retry_session.commit()

    async def geometry_profile(self, calibration_id: str):
        """Return only a verified profile; unknown/unverified IDs fail closed."""
        async with self.sessions() as session:
            record = await session.get(CalibrationProfileRecord, calibration_id)
            if record is None or not record.verified:
                return None
            return GeometryProfile(record.calibration_id, record.focal_px, record.camera_height_m)

    async def upsert_calibration_profile(
        self,
        calibration_id: str,
        focal_px: float,
        camera_height_m: float,
        *,
        verified: bool = False,
    ) -> None:
        """Create or update a mount profile. verified=True only after controlled-course sign-off."""
        async with self.sessions() as session:
            record = await session.get(CalibrationProfileRecord, calibration_id)
            if record is None:
                session.add(
                    CalibrationProfileRecord(
                        calibration_id=calibration_id,
                        focal_px=focal_px,
                        camera_height_m=camera_height_m,
                        verified=verified,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
            else:
                record.focal_px = focal_px
                record.camera_height_m = camera_height_m
                record.verified = verified
                record.updated_at = datetime.now(timezone.utc)
            await session.commit()

    async def revoke_device(self, device_id: str) -> bool:
        """Deny a device immediately without retaining any image or frame data."""
        async with self.sessions() as session:
            device = await session.get(Device, device_id)
            if device is None:
                return False
            device.revoked_at = datetime.now(timezone.utc)
            await session.commit()
            import time
            # Positive write-through: never delete-to-miss (replicas would re-read a stale False).
            self._revocation_cache[device_id] = (True, time.monotonic() + self._cache_ttl)
            if self.redis_url:
                try:
                    client = await self._get_redis_client()
                    if client:
                        await client.set(
                            "revocation:%s" % device_id,
                            b"1",
                            ex=max(int(self._cache_ttl), 60),
                        )
                except Exception:
                    logger.warning("Redis cache write failed during revocation", exc_info=True)
            return True

    async def is_device_revoked(self, device_id: str) -> bool:
        if self.redis_url:
            try:
                client = await self._get_redis_client()
                if client:
                    cached = await client.get("revocation:%s" % device_id)
                    if cached is not None:
                        return cached == b"1"
            except Exception:
                logger.warning("Redis cache lookup failed for revocation, falling back to local/db", exc_info=True)

        import time
        now = time.monotonic()
        if device_id in self._revocation_cache:
            revoked, expiry = self._revocation_cache[device_id]
            if now < expiry:
                return revoked
        async with self.sessions() as session:
            device = await session.get(Device, device_id)
            revoked = bool(device and device.revoked_at is not None)
        # Positives stick longer; negatives use a short TTL so revoke still propagates quickly.
        ttl = self._cache_ttl if revoked else self._negative_cache_ttl
        self._revocation_cache[device_id] = (revoked, now + ttl)

        if self.redis_url:
            try:
                client = await self._get_redis_client()
                if client:
                    # Positives and short-TTL negatives are both safe: revoke always overwrites with b"1".
                    await client.set(
                        "revocation:%s" % device_id,
                        b"1" if revoked else b"0",
                        ex=max(int(ttl), 1),
                    )
            except Exception:
                logger.warning("Redis cache save failed for revocation", exc_info=True)
        return revoked

    async def ping(self):
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def purge_alert_events_older_than(self, retention_days: int) -> int:
        """Enforce the documented alert-event retention window without touching device records."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        deleted = 0
        batch_size = 5_000
        while True:
            async with self.sessions() as session:
                ids = list((await session.execute(
                    select(AlertEvent.id).where(AlertEvent.created_at < cutoff).limit(batch_size)
                )).scalars())
                if not ids:
                    return deleted
                result = await session.execute(delete(AlertEvent).where(AlertEvent.id.in_(ids)))
                await session.commit()
                deleted += result.rowcount or 0

    async def record_alert(self, device_id, frame_id, hazard):
        async with self.sessions() as session:
            session.add(
                AlertEvent(
                    device_id=device_id,
                    frame_id=frame_id,
                    kind=hazard.kind,
                    level=hazard.level,
                    bearing=hazard.bearing,
                    confidence=hazard.confidence,
                    severity=getattr(hazard, 'severity', None),
                    range_band=getattr(hazard, 'range_band', None),
                    message_key=getattr(hazard, 'message_key', None),
                    track_id=getattr(hazard, 'track_id', None),
                )
            )
            await session.commit()

    async def recent_events(self, device_id, limit=20):
        async with self.sessions() as session:
            result = await session.execute(
                select(AlertEvent).where(AlertEvent.device_id == device_id).order_by(AlertEvent.id.desc()).limit(limit)
            )
            return list(result.scalars())
