import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect

from .auth import AuthError, device_id_from_token
from .config import Settings
from .domain import SessionState
from .protocol import ProtocolError, parse_frame_header, quality_for_inference
from .cloud_fallback import make_cloud_provider
from .detector import jpeg_dimensions, make_detector
from .metrics import Metrics
from .rate_limit import FrameRateLimiter
from .service import VisionService
from .storage import Store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NORMAL_FRAME_BURST = 2.0
NORMAL_FRAME_RATE_PER_SECOND = 1.2
MAX_CONTROL_MESSAGE_BYTES = 4096

settings = Settings.from_env()
store = Store(settings.database_url)
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
    ),
    store, settings.alert_max_age_ms,
)
metrics = Metrics()


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
        await store.engine.dispose()


app = FastAPI(title="Akshrava backend", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "detector": settings.detector}


@app.get("/readyz")
async def readyz():
    """Readiness is separate from liveness so a dead database removes this API from service."""
    try:
        await store.ping()
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
    await websocket.accept()
    state = SessionState(device_id=device_id)
    pending_header = None
    discard_next_binary = False
    
    # Normal walking sessions are bounded at 1.2 FPS with a two-frame burst. This matches the
    # freshness policy and prevents one authenticated device from turning server queue time into
    # stale speech for everyone else.
    limiter = FrameRateLimiter(NORMAL_FRAME_RATE_PER_SECOND, NORMAL_FRAME_BURST)
    
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
                    if pending_header is not None:
                        await websocket.send_json({"type": "error", "code": "frame_header_pending"})
                    else:
                        header = parse_frame_header(payload)
                        previous = state.last_capture_mono_ms
                        
                        if previous is not None and header.capture_mono_ms <= previous:
                            metrics.reject_frame()
                            await websocket.send_json({"type": "error", "code": "non_monotonic_capture"})
                            # Headers and JPEGs are paired without an acknowledgement. Consume the
                            # corresponding JPEG so a rejected header cannot desynchronise the socket.
                            discard_next_binary = True
                        elif not limiter.allow() or (previous is not None and (header.capture_mono_ms - previous < settings.min_frame_interval_ms)):
                            metrics.reject_frame()
                            await websocket.send_json({"type": "error", "code": "frame_rate_limited"})
                            discard_next_binary = True
                        else:
                            pending_header = header
                elif message_type == "status":
                    await websocket.send_json({"type": "status_ack"})
                else:
                    await websocket.send_json({"type": "error", "code": "unknown_message"})
            elif message.get("bytes") is not None:
                decode_started = time.monotonic()
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
                if state.calibration_id != header.calibration_id:
                    state.calibration_id = header.calibration_id
                    await store.upsert_device(device_id, header.calibration_id)
                    state.geometry_profile = await store.geometry_profile(header.calibration_id)
                state.last_capture_mono_ms = header.capture_mono_ms
                try:
                    result = await vision.analyze(state, header, jpeg)
                except Exception:
                    # Do not leave a phone believing vision is active after a model/runtime
                    # failure.  The client disables speech and reconnects only after this socket
                    # is closed, preventing a half-paired frame stream from continuing.
                    logger.exception("vision inference failed for device=%s frame_id=%s", device_id, header.frame_id)
                    await websocket.send_json({"type": "error", "code": "vision_unavailable"})
                    await websocket.close(code=1011)
                    return
                stages = dict(result.get("pipeline_stage_ms", {}))
                stages["decode"] = decode_ms
                metrics.observe_result(result["server_inference_ms"], result["hazard"] is not None, stages)
                if result.get("late_suppressed"):
                    metrics.late_suppressed()
                await websocket.send_json(result)
                
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
