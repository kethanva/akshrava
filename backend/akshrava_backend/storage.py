from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Float, Integer, String, delete, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .domain import GeometryProfile


# Both fragments form SQL grammar and must remain a closed, reviewed allow-list.  Keeping this
# separate from the migration loop prevents a later caller from accidentally interpolating a
# table, column, or type received from configuration or a request.
_ALERT_EVENT_COLUMN_ADDITIONS = {
    "severity": "VARCHAR(8)",
    "range_band": "VARCHAR(16)",
    "message_key": "VARCHAR(64)",
    "track_id": "INTEGER",
}


def _alert_event_add_column_sql(name: str) -> str:
    try:
        sql_type = _ALERT_EVENT_COLUMN_ADDITIONS[name]
    except KeyError as exc:
        raise ValueError("unsupported alert_events migration column") from exc
    return f"ALTER TABLE alert_events ADD COLUMN {name} {sql_type}"


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
    def __init__(self, url):
        self.engine = create_async_engine(url, future=True)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine.sync_engine, "connect")
            def _sqlite_pragmas(dbapi_connection, _connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def initialize(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            # `create_all` creates fresh tables but intentionally does not alter existing ones.
            # Keep the early pilot schema upgrade tiny and idempotent so a deployed SQLite file
            # can gain alert metadata without deleting its history. The column names and SQL types
            # are fixed here rather than derived from untrusted input.
            existing = await connection.run_sync(
                lambda sync_connection: {
                    column["name"] for column in inspect(sync_connection).get_columns("alert_events")
                }
            )
            for name in _ALERT_EVENT_COLUMN_ADDITIONS:
                if name not in existing:
                    await connection.execute(text(_alert_event_add_column_sql(name)))

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
