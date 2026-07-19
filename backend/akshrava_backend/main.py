import asyncio
import json
import logging
import time
import secrets
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect

from .auth import AuthError, device_id_from_token
from .application import SessionApplicationService
from .config import Settings
from .domain import SessionState
from .protocol import ProtocolError, parse_frame_header, quality_for_inference
from .cloud_fallback import make_cloud_provider
from .coordination import device_rate_limiter_for
from .detector import jpeg_dimensions, make_detector
from .metrics import Metrics
from .rate_limit import FrameRateLimiter
from .service import VisionService
from .session_admission import session_admission_for
from .storage import Store
from .gcp_storage import GcpDiagnosticStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NORMAL_FRAME_BURST = 2.0
NORMAL_FRAME_RATE_PER_SECOND = 1.2
# On-demand look frames used to bypass ALL rate limiting (header.priority is client-asserted).
# Any authenticated device -- including one stolen kit still inside its 30-day token window --
# could stamp priority=true on every frame and stream at socket speed, each frame paying a full
# validation + inference cost on the shared GPU and starving every other device's freshness
# budget. A human physically cannot long-press a headset button faster than this; the ":priority"
# key suffix gives it a separate bucket so a look burst never eats the ambient frame budget.
PRIORITY_FRAME_RATE_PER_SECOND = 0.5
PRIORITY_FRAME_BURST = 2.0
MAX_CONTROL_MESSAGE_BYTES = 4096

settings = Settings.from_env()
store = Store(
    settings.database_url,
    bootstrap_schema=settings.environment == "development",
    expected_schema_revision=None if settings.environment == "development" else settings.expected_schema_revision,
)
cloud_provider = make_cloud_provider(
    settings.cloud_fallback_provider, settings.aws_region,
    settings.azure_vision_endpoint, settings.azure_vision_key,
)
vision = VisionService(
    make_detector(
        settings.detector,
        settings.yolo_weights,
        cloud_provider,
        settings.cloud_min_confidence,
        settings.remote_inference_url,
        settings.remote_worker_secret,
        settings.remote_inference_timeout_ms,
        settings.remote_tls_ca_file,
        settings.remote_tls_client_cert_file,
        settings.remote_tls_client_key_file,
        settings.remote_inference_registry_json,
        settings.yolo_weights_sha256,
        settings.environment != "development",
    ),
    store,
    settings.alert_max_age_ms,
    inference_timeout_ms=settings.inference_timeout_ms,
    inference_executor_workers=settings.inference_executor_workers,
)
metrics = Metrics()
session_application = SessionApplicationService(store, vision)
device_rate_limiter = device_rate_limiter_for(
    redis_url=settings.redis_url,
    require_distributed=settings.environment == "production",
)
session_admission = session_admission_for(
    redis_url=settings.redis_url,
    maximum=settings.max_active_sessions,
    require_distributed=settings.environment == "production",
)
gcp_storage = GcpDiagnosticStorage(settings.gcp_diagnostics_bucket)



@asynccontextmanager
async def lifespan(app):
    await store.initialize()
    await store.purge_alert_events_older_than(settings.alert_retention_days)
    retention_stop = asyncio.Event()
    retention_task = asyncio.create_task(_retention_loop(retention_stop))
    try:
        yield
    finally:
        retention_stop.set()
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
        # Drain alert writes before disposing the DB engine so background persists are not cut off.
        shutdown_async = getattr(vision, "shutdown_async", None)
        if shutdown_async is not None:
            await shutdown_async()
        else:
            shutdown = getattr(vision, "shutdown", None)
            if shutdown is not None:
                shutdown()
        # Close GCS storage executor
        gcp_storage.close()
        await store.engine.dispose()
        await device_rate_limiter.close()
        await session_admission.shutdown()


app = FastAPI(title="Akshrava backend", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "detector": settings.detector}


@app.get("/readyz")
async def readyz():
    """Readiness is separate from liveness so a dead database removes this API from service."""
    try:
        await asyncio.wait_for(store.ping(), timeout=settings.ready_timeout_ms / 1000.0)
        if settings.environment == "production":
            await asyncio.wait_for(device_rate_limiter.health(), timeout=settings.ready_timeout_ms / 1000.0)
            await asyncio.wait_for(session_admission.health(), timeout=settings.ready_timeout_ms / 1000.0)
    except Exception:
        logger.exception("database readiness check failed")
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"ok": True, "detector": settings.detector}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    return Response(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")


async def _retention_loop(stop: asyncio.Event):
    # A restart performs cleanup immediately; a long-lived process repeats it without a cron
    # sidecar. Errors are logged and retried later rather than crashing active phone sessions.
    while not stop.is_set():
        try:
            deleted = await store.purge_alert_events_older_than(settings.alert_retention_days)
            if deleted:
                logger.info("retention cleanup removed %s alert events", deleted)
        except Exception:
            logger.exception("alert retention cleanup failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=6 * 60 * 60)
        except asyncio.TimeoutError:
            continue


def _http_device_id(authorization: Optional[str]) -> str:
    token = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else None
    try:
        return device_id_from_token(token, settings)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="invalid device token") from exc


@app.get("/v1/devices/{device_id}/events")
async def device_events(device_id: str, limit: int = 20, authorization: Optional[str] = Header(default=None)):
    # Device tokens are deliberately scoped to the device itself: this endpoint is not an
    # operator console and must never become a cross-device event feed.
    if _http_device_id(authorization) != device_id:
        raise HTTPException(status_code=403, detail="device token does not match requested device")
    if await store.is_device_revoked(device_id):
        raise HTTPException(status_code=403, detail="device access revoked")
    events = await store.recent_events(device_id, max(1, min(limit, 100)))
    return {
        "events": [
            {
                "frame_id": event.frame_id,
                "kind": event.kind,
                "level": event.level,
                "bearing": event.bearing,
                "confidence": event.confidence,
                "severity": event.severity,
                "range_band": event.range_band,
                "message_key": event.message_key,
                "track_id": event.track_id,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ]
    }


@app.websocket("/v1/session")
async def session(websocket: WebSocket):
    authorization = websocket.headers.get("authorization", "")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else None
    # Query tokens exist only for emulator/TestClient compatibility in explicit development bypass mode.
    if token is None and settings.dev_auth_bypass:
        token = websocket.query_params.get("token")
    try:
        device_id = device_id_from_token(token, settings)
    except AuthError:
        await websocket.close(code=4401)
        return
    if await store.is_device_revoked(device_id):
        await websocket.close(code=4403)
        return
    session_id = secrets.token_urlsafe(18)
    session_opened = False
    if not await session_admission.try_open(session_id):
        metrics.session_admission_rejected()
        await websocket.close(code=1013)
        return
    session_opened = True
    await websocket.accept()
    metrics.session_opened()
    consent = websocket.query_params.get("consent", "false").lower() in {"true", "1"}
    state = SessionState(
        device_id=device_id,
        trace_prefix=secrets.token_urlsafe(12),
        diagnostic_consent=consent,
    )
    pending_header = None
    discard_next_binary = False
    local_limiter = FrameRateLimiter(NORMAL_FRAME_RATE_PER_SECOND, NORMAL_FRAME_BURST)
    priority_local_limiter = FrameRateLimiter(PRIORITY_FRAME_RATE_PER_SECOND, PRIORITY_FRAME_BURST)

    # Normal walking sessions are bounded at 1.2 FPS with a two-frame burst. This matches the
    # freshness policy and prevents one authenticated device from turning server queue time into
    # stale speech for everyone else.
    try:
        # A live socket is not necessarily a vision service.  The default detector is a bench
        # transport mode, so expose that fact to the phone before it captures or reassures anyone.
        await websocket.send_json(
            {
                "type": "ready",
                "device_id": device_id,
                "max_in_flight": 1,
                "detector": settings.detector,
                "vision_enabled": settings.detector != "noop" or cloud_provider is not None,
            }
        )
        while True:
            message = await websocket.receive()
            if message.get("text") is not None:
                raw_payload = message["text"]
                if len(raw_payload.encode("utf-8")) > MAX_CONTROL_MESSAGE_BYTES:
                    raise ProtocolError("control message is too large")
                payload = json.loads(raw_payload)
                if not isinstance(payload, dict):
                    raise ProtocolError("control message must be a JSON object")
                message_type = payload.get("type")
                if message_type == "ping":
                    await websocket.send_json({"type": "pong"})
                elif message_type == "frame":
                    # A revocation can happen after this WebSocket was accepted. Check before
                    # accepting another header so a lost phone cannot continue using an already
                    # open session; the Android client treats 4403 as a permanent provisioning
                    # failure rather than reconnecting.
                    if await store.is_device_revoked(device_id):
                        await websocket.close(code=4403)
                        return
                    if pending_header is not None or discard_next_binary:
                        logger.error("Protocol violation: received header before prior binary payload was resolved")
                        await websocket.send_json({"type": "error", "code": "protocol_violation", "detail": "Header out of sequence"})
                        await websocket.close(code=4400)
                        return
                    else:
                        header = parse_frame_header(payload)
                        previous = state.last_capture_mono_ms
                        
                        if previous is not None and header.capture_mono_ms <= previous:
                            metrics.reject_frame()
                            await websocket.send_json({"type": "error", "code": "non_monotonic_capture"})
                            # Headers and JPEGs are paired without an acknowledgement. Consume the
                            # corresponding JPEG so a rejected header cannot desynchronise the socket.
                            discard_next_binary = True
                        else:
                            # Priority (on-demand look) frames get their own, tighter bucket
                            # instead of bypassing rate limiting entirely -- header.priority is
                            # client-asserted, so an unbounded bypass would let any authenticated
                            # device (including a lost/stolen kit still inside its token window)
                            # flood the shared GPU at socket speed. The ":priority" key suffix
                            # keeps this bucket fully separate from the ambient-frame bucket, so
                            # a look burst never eats into ordinary walking-session budget.
                            if header.priority:
                                rate_id = "%s:priority" % device_id
                                rate_per_second, burst = PRIORITY_FRAME_RATE_PER_SECOND, PRIORITY_FRAME_BURST
                                dev_limiter = priority_local_limiter
                            else:
                                rate_id = device_id
                                rate_per_second, burst = NORMAL_FRAME_RATE_PER_SECOND, NORMAL_FRAME_BURST
                                dev_limiter = local_limiter
                            try:
                                rate_allowed = (
                                    await device_rate_limiter.allow(rate_id, rate_per_second, burst)
                                    if settings.environment == "production"
                                    else dev_limiter.allow()
                                )
                            except Exception:
                                logger.exception("frame admission control unavailable")
                                await websocket.send_json({"type": "error", "code": "vision_unavailable"})
                                await websocket.close(code=1011)
                                return
                            # A look frame right after an ambient frame is the whole point of the
                            # feature (the standing-still blind spot), so the min-interval floor
                            # -- which exists to bound ambient upload cadence -- does not apply
                            # to priority frames; the priority token bucket above already bounds
                            # how often a look can happen.
                            if not rate_allowed or (
                                not header.priority
                                and previous is not None
                                and header.capture_mono_ms - previous < settings.min_frame_interval_ms
                            ):
                                metrics.reject_frame()
                                await websocket.send_json({"type": "error", "code": "frame_rate_limited"})
                                discard_next_binary = True
                            else:
                                pending_header = header
                elif message_type == "status":
                    await websocket.send_json({"type": "status_ack"})
                elif message_type == "look":
                    # On-demand look is a priority frame header+binary; acknowledge intent.
                    await websocket.send_json({"type": "look_ack"})
                else:
                    await websocket.send_json({"type": "error", "code": "unknown_message"})
            elif message.get("bytes") is not None:
                decode_started = time.monotonic()
                if not (pending_header is not None or discard_next_binary):
                    logger.error("Protocol violation: received binary bytes without pending header")
                    await websocket.send_json({"type": "error", "code": "protocol_violation", "detail": "Binary payload out of sequence"})
                    await websocket.close(code=4400)
                    return
                if discard_next_binary:
                    discard_next_binary = False
                    continue
                if pending_header is None:
                    metrics.reject_frame()
                    await websocket.send_json({"type": "error", "code": "missing_frame_header"})
                    continue
                jpeg = message["bytes"]
                header = pending_header
                pending_header = None
                if len(jpeg) != header.jpeg_bytes or len(jpeg) > settings.max_image_bytes:
                    metrics.reject_frame()
                    await websocket.send_json({"type": "error", "code": "invalid_image_size"})
                    continue
                if header.width > settings.max_frame_side or header.height > settings.max_frame_side:
                    metrics.reject_frame()
                    await websocket.send_json({"type": "error", "code": "unsupported_frame_size"})
                    continue
                try:
                    actual_width, actual_height = jpeg_dimensions(jpeg)
                except ValueError:
                    metrics.reject_frame()
                    await websocket.send_json({"type": "error", "code": "invalid_jpeg"})
                    continue
                if (actual_width, actual_height) != (header.width, header.height):
                    metrics.reject_frame()
                    await websocket.send_json({"type": "error", "code": "jpeg_dimension_mismatch"})
                    continue
                decode_ms = int((time.monotonic() - decode_started) * 1000)
                try:
                    result = await session_application.analyze_frame(state, header, jpeg)
                except Exception:
                    # Do not leave a phone believing vision is active after a model/runtime
                    # failure.  The client disables speech and reconnects only after this socket
                    # is closed, preventing a half-paired frame stream from continuing.
                    logger.exception("vision inference failed for device=%s frame_id=%s", device_id, header.frame_id)
                    metrics.inference_failed()
                    await websocket.send_json({"type": "error", "code": "vision_unavailable"})
                    await websocket.close(code=1011)
                    return
                stages = dict(result.get("pipeline_stage_ms", {}))
                stages["decode"] = decode_ms
                metrics.observe_result(result["server_inference_ms"], result["hazard"] is not None, stages)
                if header.capture_epoch_ms is not None:
                    frame_age_ms = int(result["server_received_epoch_ms"]) - header.capture_epoch_ms
                    if 0 <= frame_age_ms <= 60_000:
                        metrics.observe_frame_age(frame_age_ms)
                if result.get("late_suppressed"):
                    metrics.late_suppressed()
                await websocket.send_json(result)
                if state.diagnostic_consent and settings.gcp_diagnostics_bucket:
                    file_name = f"{device_id}/{header.frame_id}_{header.capture_mono_ms}.jpg"
                    asyncio.create_task(gcp_storage.upload_frame(file_name, jpeg))
                
                await websocket.send_json(quality_for_inference(result["server_inference_ms"]))
            else:
                await websocket.send_json({"type": "error", "code": "unsupported_message"})
    except (WebSocketDisconnect, RuntimeError):
        logger.info("session closed for device=%s", device_id)
    except (ProtocolError, json.JSONDecodeError) as exc:
        logger.warning("protocol error for device=%s: %s", device_id, exc)
        # The socket may already be broken by the same condition that raised ProtocolError
        # (e.g. the peer vanished mid-message). Best-effort notify-then-close; a failure here
        # is just the disconnect racing us and must not surface as an unhandled exception.
        with suppress(RuntimeError):
            await websocket.send_json({"type": "error", "code": "protocol_error", "detail": str(exc)})
            await websocket.close(code=4400)
    finally:
        if session_opened:
            await session_admission.close(session_id)
            metrics.session_closed()
        await session_application.close_session(state)
