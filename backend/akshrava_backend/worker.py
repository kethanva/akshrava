"""Private GPU inference worker for the split control-plane deployment.

This service deliberately exposes no phone WebSocket, database, event history, or operator API.
It accepts only an HMAC-authenticated image from a configured control plane and returns detector
boxes. Deploy it behind a private network or mutually authenticated reverse proxy; the HMAC is
defence in depth, not a replacement for network isolation.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response

from .detector import Detector, jpeg_dimensions, make_detector
from .metrics import Metrics


@dataclass(frozen=True)
class WorkerSettings:
    shared_secret: str
    yolo_weights: str
    max_image_bytes: int
    max_frame_side: int
    request_max_age_seconds: int
    require_gpu: bool = True
    batch_max_size: int = 8
    batch_wait_ms: int = 12

    @classmethod
    def from_env(cls):
        settings = cls(
            shared_secret=os.getenv("WORKER_SHARED_SECRET", ""),
            yolo_weights=os.getenv("YOLO_WEIGHTS", "/models/yolo11s.pt"),
            max_image_bytes=int(os.getenv("MAX_IMAGE_BYTES", "200000")),
            max_frame_side=int(os.getenv("MAX_FRAME_SIDE", "1280")),
            request_max_age_seconds=int(os.getenv("WORKER_REQUEST_MAX_AGE_SECONDS", "30")),
            require_gpu=os.getenv("REQUIRE_GPU", "true").lower() in {"1", "true", "yes", "on"},
            batch_max_size=int(os.getenv("WORKER_BATCH_MAX_SIZE", "8")),
            batch_wait_ms=int(os.getenv("WORKER_BATCH_WAIT_MS", "12")),
        )
        if len(settings.shared_secret) < 32:
            raise ValueError("WORKER_SHARED_SECRET must be at least 32 characters")
        if settings.max_image_bytes < 1:
            raise ValueError("MAX_IMAGE_BYTES must be positive")
        if settings.max_frame_side < 1:
            raise ValueError("MAX_FRAME_SIDE must be positive")
        if not 5 <= settings.request_max_age_seconds <= 300:
            raise ValueError("WORKER_REQUEST_MAX_AGE_SECONDS must be between 5 and 300")
        if not 1 <= settings.batch_max_size <= 64:
            raise ValueError("WORKER_BATCH_MAX_SIZE must be between 1 and 64")
        if not 0 <= settings.batch_wait_ms <= 50:
            raise ValueError("WORKER_BATCH_WAIT_MS must be between 0 and 50")
        return settings


async def _batch_loop(app: FastAPI):
    """Coalesce a short burst into one detector invocation without mixing responses."""
    queue = app.state.inference_queue
    settings = app.state.worker_settings
    while True:
        jpeg, future = await queue.get()
        batch = [(jpeg, future)]
        deadline = time.monotonic() + settings.batch_wait_ms / 1000.0
        while len(batch) < settings.batch_max_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(queue.get(), remaining))
            except asyncio.TimeoutError:
                break
        try:
            detections = await asyncio.get_running_loop().run_in_executor(
                None, app.state.worker_detector.detect_batch, [item[0] for item in batch]
            )
            if len(detections) != len(batch):
                raise RuntimeError("detector batch response length mismatch")
            for (_, item_future), item_detections in zip(batch, detections):
                if not item_future.cancelled():
                    item_future.set_result(item_detections)
        except Exception as exc:
            for _, item_future in batch:
                if not item_future.cancelled():
                    item_future.set_exception(exc)


def _authenticated_body(request: Request, body: bytes, settings: WorkerSettings):
    timestamp = request.headers.get("x-akshrava-timestamp", "")
    nonce = request.headers.get("x-akshrava-nonce", "")
    signature = request.headers.get("x-akshrava-signature", "")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="missing or invalid worker timestamp") from exc
    if abs(time.time() - timestamp_value) > settings.request_max_age_seconds:
        raise HTTPException(status_code=401, detail="expired worker request")
    if not 16 <= len(nonce) <= 128 or not nonce.isascii():
        raise HTTPException(status_code=401, detail="missing or invalid worker nonce")
    expected = hmac.new(
        settings.shared_secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid worker signature")
    return nonce


def create_worker_app(
    settings: Optional[WorkerSettings] = None,
    detector: Optional[Detector] = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configured_settings = settings or WorkerSettings.from_env()
        if configured_settings.require_gpu:
            try:
                import torch
            except ImportError as exc:
                raise RuntimeError("GPU worker requires a CUDA-enabled PyTorch runtime") from exc
            if not torch.cuda.is_available():
                raise RuntimeError("GPU worker started without CUDA; refusing CPU inference")
        configured_detector = detector or make_detector("ultralytics", configured_settings.yolo_weights)
        app.state.worker_settings = configured_settings
        app.state.worker_detector = configured_detector
        app.state.inference_queue = asyncio.Queue(maxsize=configured_settings.batch_max_size * 8)
        app.state.batch_task = asyncio.create_task(_batch_loop(app))
        app.state.nonce_lock = asyncio.Lock()
        app.state.used_nonces = {}
        app.state.metrics = Metrics()
        try:
            yield
        finally:
            app.state.batch_task.cancel()
            try:
                await app.state.batch_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Akshrava GPU inference worker", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "role": "gpu-worker"}

    @app.get("/readyz")
    async def readyz():
        return {"ok": True, "role": "gpu-worker", "detector": "ultralytics"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics():
        return Response(app.state.metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.post("/v1/infer")
    async def infer(request: Request):
        body = await request.body()
        # Base64 expands 200 KB images by approximately one third; this hard cap prevents an
        # authenticated control plane from accidentally exhausting a small worker.
        settings_value = app.state.worker_settings
        if len(body) > settings_value.max_image_bytes * 2:
            raise HTTPException(status_code=413, detail="worker request too large")
        nonce = _authenticated_body(request, body, settings_value)
        now = time.time()
        async with app.state.nonce_lock:
            cutoff = now - settings_value.request_max_age_seconds
            app.state.used_nonces = {
                value: seen_at for value, seen_at in app.state.used_nonces.items() if seen_at >= cutoff
            }
            if nonce in app.state.used_nonces:
                raise HTTPException(status_code=409, detail="replayed worker request")
            app.state.used_nonces[nonce] = now
        try:
            payload = json.loads(body)
            image_b64 = payload["image_b64"]
            if not isinstance(image_b64, str):
                raise ValueError("image_b64 must be text")
            jpeg = base64.b64decode(image_b64, validate=True)
            if len(jpeg) > settings_value.max_image_bytes:
                raise ValueError("image too large")
            width, height = jpeg_dimensions(jpeg)
            if width > settings_value.max_frame_side or height > settings_value.max_frame_side:
                raise ValueError("image dimensions too large")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="invalid inference image") from exc

        started = time.monotonic()
        future = asyncio.get_running_loop().create_future()
        try:
            app.state.inference_queue.put_nowait((jpeg, future))
        except asyncio.QueueFull as exc:
            raise HTTPException(status_code=503, detail="worker inference queue full") from exc
        detections = await future
        inference_ms = int((time.monotonic() - started) * 1000)
        app.state.metrics.observe_result(inference_ms, False)
        return {
            "detections": [
                {"label": item.label, "confidence": item.confidence, "box": list(item.box)}
                for item in detections[:100]
            ]
        }

    return app


app = create_worker_app()
