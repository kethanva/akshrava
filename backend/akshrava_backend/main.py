import asyncio
import hmac
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from json import JSONDecodeError
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Response, WebSocket, WebSocketDisconnect

from .auth import AuthError, device_claims_from_token, device_id_from_token
from .application import SessionApplicationService
from .config import Settings
from .domain import SessionState
from .protocol import ProtocolError, quality_for_inference
from .cloud_fallback import make_cloud_provider
from .coordination import device_rate_limiter_for
from .detector import make_detector
from .logging_util import configure_json_logging
from .metrics import Metrics
from .rate_limit import FrameRateLimiter
from .service import VisionService
from .session_admission import session_admission_for
from .storage import Store
from .gcp_storage import GcpDiagnosticStorage
from .session_handler import MAX_CONTROL_MESSAGE_BYTES, FrameStreamHandler  # noqa: F401 — re-exported operational limit
from .tracing import ensure_tracer_provider

configure_json_logging()
logger = logging.getLogger(__name__)
ensure_tracer_provider()

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

settings = Settings.from_env()
store = Store(
    settings.database_url,
    redis_url=settings.redis_url,
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
    require_distributed=settings.environment != "development",
)
session_admission = session_admission_for(
    redis_url=settings.redis_url,
    maximum=settings.max_active_sessions,
    require_distributed=settings.environment != "development",
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
        await store.close()
        await device_rate_limiter.close()
        await session_admission.shutdown()


app = FastAPI(title="Akshrava backend", version="0.1.0", lifespan=lifespan)


@app.get("/livez")
async def livez():
    """Process liveness for probes. Prefer /livez over /healthz on *.run.app (GFE reserves /healthz)."""
    return {"ok": True, "detector": settings.detector}


@app.get("/healthz")
async def healthz():
    # Kept for Compose/local probes. On Cloud Run *.run.app, GFE may intercept /healthz — use /livez.
    return await livez()


@app.get("/readyz")
async def readyz():
    """Readiness is separate from liveness so a dead database removes this API from service."""
    try:
        await asyncio.wait_for(store.ping(), timeout=settings.ready_timeout_ms / 1000.0)
        # Pilot and production both depend on Redis for admission + rate limits when REDIS_URL is set.
        if settings.environment != "development" or settings.redis_url:
            await asyncio.wait_for(device_rate_limiter.health(), timeout=settings.ready_timeout_ms / 1000.0)
            await asyncio.wait_for(session_admission.health(), timeout=settings.ready_timeout_ms / 1000.0)
    except Exception:
        logger.exception("readiness check failed")
        raise HTTPException(status_code=503, detail="dependencies unavailable")
    return {"ok": True, "detector": settings.detector}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    x_akshrava_metrics_token: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    # Match Compose Caddy: do not expose aggregate metrics on the public URL outside development.
    # Internal scrapers pass METRICS_SCRAPE_TOKEN via header or Bearer.
    if settings.environment != "development":
        expected = settings.metrics_scrape_token
        provided = (x_akshrava_metrics_token or "").strip()
        if not provided and authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        if (
            not expected
            or not provided
            or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
        ):
            raise HTTPException(status_code=404, detail="not found")
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


async def _renew_or_readmit(session_id: str) -> bool:
    """Keep a live socket admitted across a lapsed lease.

    The Redis admission lease is short (~3 min) and is only refreshed by app-level traffic.
    A perfectly healthy but *quiet* session -- a stationary user at 0.2 FPS whose frames are
    all duplicate-dropped on the phone -- legitimately sends nothing for longer than the lease,
    so renew() failing must not be treated as an eviction: the connection is alive and the user
    is mid-walk. Re-admit through try_open() (idempotent for an id already present) and only
    close when fleet capacity is genuinely exhausted.
    """
    if await session_admission.renew(session_id):
        return True
    return await session_admission.try_open(session_id)


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
        claims = device_claims_from_token(token, settings)
        device_id = claims.device_id
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
    # Diagnostic upload consent is server-side (JWT claim). Query param alone is never enough;
    # development may OR a query flag only when DEV_AUTH_BYPASS is on for local bench uploads.
    consent = claims.diagnostic_consent
    if settings.dev_auth_bypass and websocket.query_params.get("consent", "false").lower() in {"true", "1"}:
        consent = True
    state = SessionState(
        device_id=device_id,
        session_key=session_id,
        trace_prefix=secrets.token_urlsafe(12),
        diagnostic_consent=consent,
    )
    handler = FrameStreamHandler(
        device_id=device_id,
        state=state,
        settings=settings,
        store=store,
        device_rate_limiter=device_rate_limiter,
        metrics=metrics,
        local_limiter=FrameRateLimiter(NORMAL_FRAME_RATE_PER_SECOND, NORMAL_FRAME_BURST),
        priority_local_limiter=FrameRateLimiter(PRIORITY_FRAME_RATE_PER_SECOND, PRIORITY_FRAME_BURST),
        normal_rate=NORMAL_FRAME_RATE_PER_SECOND,
        normal_burst=NORMAL_FRAME_BURST,
        priority_rate=PRIORITY_FRAME_RATE_PER_SECOND,
        priority_burst=PRIORITY_FRAME_BURST,
    )

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
                resp = await handler.handle_text_frame(message["text"])
                if resp is not None:
                    if resp.get("_action") == "close":
                        if resp.get("response") is not None:
                            await websocket.send_json(resp["response"])
                        await websocket.close(code=resp["code"])
                        return
                    # Keep admission lease alive on ping / control traffic.
                    if not await _renew_or_readmit(session_id):
                        await websocket.close(code=1013)
                        return
                    await websocket.send_json(resp)
            elif message.get("bytes") is not None:
                resp = await handler.handle_binary_frame(message["bytes"])
                if resp.get("_action") == "close":
                    if resp.get("response") is not None:
                        await websocket.send_json(resp["response"])
                    await websocket.close(code=resp["code"])
                    return
                elif resp.get("_action") == "continue":
                    continue
                elif resp.get("_action") == "analyze":
                    if not await _renew_or_readmit(session_id):
                        await websocket.close(code=1013)
                        return
                    header = resp["header"]
                    decode_ms = resp["decode_ms"]
                    jpeg = message["bytes"]

                    try:
                        result = await session_application.analyze_frame(state, header, jpeg)
                    except Exception as exc:
                        from .detector import WorkerSaturatedError

                        # Soft-shed under worker overload: keep the socket open so the phone
                        # can retry the next frame instead of believing vision is permanently dead.
                        if isinstance(exc, WorkerSaturatedError) or "circuit open" in str(exc):
                            metrics.worker_saturated()
                            await websocket.send_json({"type": "error", "code": "worker_saturated"})
                            continue
                        # Hard failures (model/runtime) still fail closed.
                        logger.exception(
                            "vision inference failed for device=%s frame_id=%s",
                            device_id,
                            header.frame_id,
                        )
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
                    # Fail-closed: JWT consent + bucket are not enough until blur exists (docs/README.md (privacy)).
                    if (
                        settings.diagnostic_uploads_enabled
                        and state.diagnostic_consent
                        and settings.gcp_diagnostics_bucket
                    ):
                        file_name = f"{device_id}/{header.frame_id}_{header.capture_mono_ms}.jpg"

                        async def _upload_diagnostic(name=file_name, payload=jpeg):
                            try:
                                await gcp_storage.upload_frame(name, payload)
                            except Exception:
                                logger.exception("diagnostic upload failed device=%s", device_id)

                        vision.schedule_diagnostic_upload(_upload_diagnostic())

                    await websocket.send_json(quality_for_inference(result["server_inference_ms"]))
                else:
                    await websocket.send_json(resp)
            else:
                await websocket.send_json({"type": "error", "code": "unsupported_message"})
    except (WebSocketDisconnect, RuntimeError):
        logger.info("session closed for device=%s", device_id)
    except (ProtocolError, JSONDecodeError) as exc:
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
