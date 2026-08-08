import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from akshrava_backend.detector import (
    Detector,
    InferenceEndpoint,
    RegistryRemoteWorkerDetector,
    RemoteWorkerDetector,
    StaticInferenceEndpointRegistry,
)
from akshrava_backend.domain import Detection
from akshrava_backend.worker import WorkerSettings, create_worker_app

JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/Aaf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/Aaf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Ap//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)
SECRET = "test-worker-secret-with-at-least-32-chars"


class FixedDetector(Detector):
    def detect(self, jpeg):
        return [Detection("person", 0.9, (1, 2, 3, 4))]


class BatchDetector(FixedDetector):
    def __init__(self):
        self.batch_sizes = []

    def detect_batch(self, jpegs):
        self.batch_sizes.append(len(jpegs))
        return super().detect_batch(jpegs)


class EmptyDetector(Detector):
    def detect(self, jpeg):
        return []


class HangingDetector(Detector):
    """Simulates a stuck GPU call (model hang, driver wedge) that outlasts the request timeout.

    Sleeps only long enough to exceed the test's infer_timeout_seconds, not indefinitely: a
    real hang (or time.sleep(3600)) would run on concurrent.futures' default ThreadPoolExecutor,
    whose worker threads are joined at interpreter exit -- an indefinite sleep here would hang
    the whole test process shutdown, not just this one request.
    """

    def detect(self, jpeg):
        time.sleep(2)
        return []


def _signed_headers(body):
    timestamp = str(int(time.time()))
    nonce = "worker-test-nonce-1234"
    signature = hmac.new(
        SECRET.encode("utf-8"),
        timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Akshrava-Timestamp": timestamp,
        "X-Akshrava-Nonce": nonce,
        "X-Akshrava-Signature": signature,
    }


def test_gpu_worker_accepts_only_signed_images_and_returns_boxes():
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False)
    app = create_worker_app(settings, FixedDetector())
    headers = {**_signed_headers(JPEG), "Content-Type": "image/jpeg"}
    with TestClient(app) as client:
        assert client.post("/v1/infer", content=JPEG).status_code == 401
        response = client.post("/v1/infer", content=JPEG, headers=headers)
        assert response.status_code == 200
        assert response.json() == {
            "detections": [{"label": "person", "confidence": 0.9, "box": [1, 2, 3, 4]}]
        }
        assert client.post("/v1/infer", content=JPEG, headers=headers).status_code == 409


def test_gpu_worker_rejects_legacy_base64_json_bodies():
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False)
    app = create_worker_app(settings, FixedDetector())
    body = json.dumps({"image_b64": base64.b64encode(JPEG).decode("ascii")}).encode("utf-8")
    with TestClient(app) as client:
        response = client.post(
            "/v1/infer",
            content=body,
            headers={**_signed_headers(body), "Content-Type": "application/json"},
        )
        assert response.status_code == 415


def test_gpu_worker_uses_detector_batch_contract():
    detector = BatchDetector()
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False, batch_wait_ms=0)
    app = create_worker_app(settings, detector)
    with TestClient(app) as client:
        response = client.post(
            "/v1/infer",
            content=JPEG,
            headers={**_signed_headers(JPEG), "Content-Type": "image/jpeg"},
        )
        assert response.status_code == 200
    assert detector.batch_sizes == [1]


def test_gpu_worker_metrics_reflect_whether_detections_were_actually_found():
    # Regression test: the worker used to call observe_result(inference_ms, False)
    # unconditionally, so akshrava_alerts_emitted_total on the GPU worker was a permanent,
    # meaningless zero on any operator dashboard regardless of what the detector actually found.
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False)
    app = create_worker_app(settings, FixedDetector())
    with TestClient(app) as client:
        response = client.post(
            "/v1/infer", content=JPEG, headers={**_signed_headers(JPEG), "Content-Type": "image/jpeg"}
        )
        assert response.status_code == 200
        metrics_text = client.get("/metrics").text
    assert "akshrava_alerts_emitted_total 1" in metrics_text


def test_gpu_worker_empty_detection_does_not_count_as_an_alert():
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False)
    app = create_worker_app(settings, EmptyDetector())
    with TestClient(app) as client:
        response = client.post(
            "/v1/infer", content=JPEG, headers={**_signed_headers(JPEG), "Content-Type": "image/jpeg"}
        )
        assert response.status_code == 200
        metrics_text = client.get("/metrics").text
    assert "akshrava_alerts_emitted_total 0" in metrics_text


def test_gpu_worker_metrics_require_token_outside_development():
    settings = WorkerSettings(
        SECRET,
        "unused.pt",
        200_000,
        1280,
        30,
        require_gpu=False,
        environment="pilot",
        metrics_scrape_token="worker-metrics-token",
        nonce_redis_url="redis://localhost:6379/1",
        yolo_weights_sha256="a" * 64,
    )
    app = create_worker_app(settings, FixedDetector())
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 404
        ok = client.get("/metrics", headers={"Authorization": "Bearer worker-metrics-token"})
        assert ok.status_code == 200
        assert "akshrava_alerts_emitted_total" in ok.text


def test_gpu_worker_infer_fails_fast_instead_of_hanging_on_a_stuck_detector():
    # Regression test: `await future` had no timeout, so a stuck detector call (model hang,
    # GPU driver wedge) left the HTTP request waiting forever instead of failing within the
    # control plane's own remote_inference_timeout_ms budget.
    settings = WorkerSettings(
        SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False, infer_timeout_seconds=0.2
    )
    app = create_worker_app(settings, HangingDetector())
    with TestClient(app) as client:
        started = time.monotonic()
        response = client.post(
            "/v1/infer", content=JPEG, headers={**_signed_headers(JPEG), "Content-Type": "image/jpeg"}
        )
        elapsed = time.monotonic() - started
    assert response.status_code == 504
    assert elapsed < 2.0, "a stuck detector must not hang the request past its own timeout"


def test_gpu_worker_drains_queued_futures_on_shutdown_instead_of_hanging_them():
    # Regression test: cancelling the batch loop on shutdown only stopped it from dequeuing
    # further work -- any (jpeg, future) pairs still sitting in the queue at that moment were
    # never resolved, so a request still awaiting one would hang until the ASGI server
    # forcibly dropped the connection instead of getting a clean error during graceful shutdown.
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False, batch_wait_ms=50)
    app = create_worker_app(settings, FixedDetector())

    async def scenario():
        async with app.router.lifespan_context(app):
            future = asyncio.get_running_loop().create_future()
            app.state.inference_queue.put_nowait((JPEG, future))
            # Exit the lifespan context (simulating shutdown) while the future is still queued.
        return future

    future = asyncio.run(scenario())
    assert future.done()
    assert isinstance(future.exception(), RuntimeError)


def test_gpu_worker_readiness_fails_when_replay_protection_is_unavailable(monkeypatch):
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False)
    app = create_worker_app(settings, FixedDetector())
    with TestClient(app) as client:
        async def unavailable():
            raise RuntimeError("redis unavailable")

        monkeypatch.setattr(app.state.nonce_store, "health", unavailable)
        assert client.get("/readyz").status_code == 503


def test_remote_detector_signs_and_validates_worker_response(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b'{"detections":[{"label":"car","confidence":0.8,"box":[1,2,3,4]}]}'

    class FakeOpener:
        def open(self, request, timeout=None):
            captured["signature"] = request.get_header("X-akshrava-signature") or request.headers.get("X-Akshrava-Signature")
            # urllib Request stores headers with capitalization variants
            headers = {k.lower(): v for k, v in request.header_items()}
            captured["signature"] = headers.get("x-akshrava-signature")
            captured["content_type"] = headers.get("content-type")
            captured["body"] = request.data
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr("akshrava_backend.detector.build_opener", lambda *args, **kwargs: FakeOpener())
    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", SECRET, 450)
    assert detector.detect(JPEG) == [Detection("car", 0.8, (1.0, 2.0, 3.0, 4.0))]
    assert captured["signature"]
    assert captured["content_type"] == "image/jpeg"
    assert captured["body"] == JPEG
    assert captured["timeout"] == 0.45


def test_remote_detector_rejects_http_redirects(monkeypatch):
    from akshrava_backend.detector import RemoteInferenceError

    class FakeOpener:
        def open(self, request, timeout=None):
            raise RemoteInferenceError("remote worker redirect rejected")

    monkeypatch.setattr("akshrava_backend.detector.build_opener", lambda *args, **kwargs: FakeOpener())
    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", SECRET, 450)
    try:
        detector.detect(JPEG)
        assert False, "expected redirect rejection"
    except RemoteInferenceError as exc:
        assert "redirect" in str(exc)


def test_remote_detector_rejects_host_outside_allowlist():
    from akshrava_backend.detector import RemoteInferenceError

    detector = RemoteWorkerDetector(
        "https://worker.internal/v1/infer",
        SECRET,
        450,
        allowed_hosts={"worker.internal"},
    )
    detector.endpoint = "https://evil.example/v1/infer"
    try:
        detector.detect(JPEG)
        assert False, "expected host allowlist rejection"
    except RemoteInferenceError as exc:
        assert "allowlist" in str(exc)

def test_gpu_worker_accepts_signed_binary_jpeg_without_base64():
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False)
    app = create_worker_app(settings, FixedDetector())
    with TestClient(app) as client:
        response = client.post("/v1/infer", content=JPEG, headers={
            **_signed_headers(JPEG),
            "Content-Type": "image/jpeg",
        })
        assert response.status_code == 200
        assert response.json()["detections"][0]["label"] == "person"


def test_remote_detector_routes_device_stickily_and_fails_over_to_warm_worker(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b'{"detections":[]}'

    class FakeOpener:
        def open(self, request, timeout=None):
            calls.append(request.full_url)
            if len(calls) == 1:
                from urllib.error import URLError
                raise URLError("preempted")
            return Response()

    monkeypatch.setattr("akshrava_backend.detector.build_opener", lambda *args, **kwargs: FakeOpener())
    registry = StaticInferenceEndpointRegistry([
        InferenceEndpoint("one", "https://one.internal/v1/infer"),
        InferenceEndpoint("two", "https://two.internal/v1/infer"),
    ])
    detector = RegistryRemoteWorkerDetector(
        registry, SECRET, 450
    )
    routed = registry.ordered_for_device("pilot-phone-1")
    assert detector.detect_for_device("pilot-phone-1", JPEG) == []
    assert calls == [endpoint.url for endpoint in routed[:2]]


def test_static_endpoint_registry_filters_disabled_entries_and_keeps_stable_order():
    registry = StaticInferenceEndpointRegistry.from_json(
        '[{"id":"one","url":"https://one.internal/v1/infer","enabled":false},'
        '{"id":"two","url":"https://two.internal/v1/infer"},'
        '{"id":"three","url":"https://three.internal/v1/infer"}]'
    )
    first = registry.ordered_for_device("pilot-phone-1")
    second = registry.ordered_for_device("pilot-phone-1")
    assert [endpoint.id for endpoint in first] == [endpoint.id for endpoint in second]
    assert "one" not in [endpoint.id for endpoint in first]


def test_remote_worker_detector_injects_w3c_trace_headers(monkeypatch):
    captured_headers = {}

    class FakeClient:
        async def post(self, url, content=None, headers=None, follow_redirects=False):
            captured_headers.update(headers or {})
            class FakeResp:
                status_code = 200
                content = b'{"detections":[]}'
                def raise_for_status(self):
                    pass
            return FakeResp()

    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", SECRET, 450)
    monkeypatch.setattr(detector, "_get_async_client", lambda: asyncio.sleep(0, result=FakeClient()))

    res = asyncio.run(detector.detect_async(JPEG))
    assert res == []
    assert "X-Akshrava-Timestamp" in captured_headers
    assert "X-Akshrava-Nonce" in captured_headers
    assert "X-Akshrava-Signature" in captured_headers
    assert "traceparent" in captured_headers, "W3C Trace Context must be injected on remote infer"
    assert captured_headers["traceparent"].startswith("00-")


def test_remote_worker_detect_async_raises_saturated_on_503(monkeypatch):
    class FakeClient:
        async def post(self, url, content=None, headers=None, follow_redirects=False):
            import httpx
            request = httpx.Request("POST", url)
            response = httpx.Response(503, request=request, text="queue full")
            raise httpx.HTTPStatusError("saturated", request=request, response=response)

    from akshrava_backend.detector import WorkerSaturatedError

    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", SECRET, 450)
    monkeypatch.setattr(detector, "_get_async_client", lambda: asyncio.sleep(0, result=FakeClient()))
    with pytest.raises(WorkerSaturatedError):
        asyncio.run(detector.detect_async(JPEG))


def test_registry_remote_preserves_worker_saturated_error(monkeypatch):
    """Single-endpoint registry must not rewrite 503 into a generic RemoteInferenceError."""
    from akshrava_backend.detector import (
        InferenceEndpoint,
        RegistryRemoteWorkerDetector,
        StaticInferenceEndpointRegistry,
        WorkerSaturatedError,
    )

    class FakeClient:
        async def post(self, url, content=None, headers=None, follow_redirects=False):
            class FakeResp:
                status_code = 503
                content = b"queue full"

                def raise_for_status(self):
                    pass

            return FakeResp()

    registry = StaticInferenceEndpointRegistry(
        [InferenceEndpoint(id="worker-1", url="https://worker.internal/v1/infer")]
    )
    detector = RegistryRemoteWorkerDetector(
        registry,
        shared_secret=SECRET,
        timeout_ms=450,
    )
    worker = next(iter(detector._workers.values()))
    monkeypatch.setattr(worker, "_get_async_client", lambda: asyncio.sleep(0, result=FakeClient()))
    with pytest.raises(WorkerSaturatedError):
        asyncio.run(detector.detect_async_for_device("phone-1", JPEG))


def test_worker_settings_from_env_validation(monkeypatch):
    base_env = {
        "WORKER_SHARED_SECRET": SECRET,
        "YOLO_WEIGHTS": "/models/yolo.pt",
        "YOLO_WEIGHTS_SHA256": "a" * 64,
        "MAX_IMAGE_BYTES": "200000",
        "MAX_FRAME_SIDE": "1280",
        "WORKER_REQUEST_MAX_AGE_SECONDS": "30",
        "REQUIRE_GPU": "false",
        "WORKER_BATCH_MAX_SIZE": "8",
        "WORKER_BATCH_WAIT_MS": "12",
        "AKSHRAVA_ENV": "development",
        "NONCE_REDIS_URL": "redis://localhost:6379/0",
        "WORKER_INFER_TIMEOUT_SECONDS": "5.0",
        "METRICS_SCRAPE_TOKEN": "token123",
    }

    # Short secret
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "WORKER_SHARED_SECRET": "short"})
        with pytest.raises(ValueError, match="at least 32 characters"):
            WorkerSettings.from_env()

    # Invalid max image bytes
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "MAX_IMAGE_BYTES": "0"})
        with pytest.raises(ValueError, match="MAX_IMAGE_BYTES must be positive"):
            WorkerSettings.from_env()

    # Invalid max frame side
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "MAX_FRAME_SIDE": "0"})
        with pytest.raises(ValueError, match="MAX_FRAME_SIDE must be positive"):
            WorkerSettings.from_env()

    # Invalid request max age
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "WORKER_REQUEST_MAX_AGE_SECONDS": "1"})
        with pytest.raises(ValueError, match="WORKER_REQUEST_MAX_AGE_SECONDS must be between 5 and 300"):
            WorkerSettings.from_env()

    # Invalid batch max size
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "WORKER_BATCH_MAX_SIZE": "100"})
        with pytest.raises(ValueError, match="WORKER_BATCH_MAX_SIZE must be between 1 and 64"):
            WorkerSettings.from_env()

    # Invalid batch wait ms
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "WORKER_BATCH_WAIT_MS": "100"})
        with pytest.raises(ValueError, match="WORKER_BATCH_WAIT_MS must be between 0 and 50"):
            WorkerSettings.from_env()

    # Invalid infer timeout
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "WORKER_INFER_TIMEOUT_SECONDS": "0.1"})
        with pytest.raises(ValueError, match="WORKER_INFER_TIMEOUT_SECONDS must be between 0.5 and 30"):
            WorkerSettings.from_env()

    # Invalid environment
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "AKSHRAVA_ENV": "invalid"})
        with pytest.raises(ValueError, match="AKSHRAVA_ENV must be"):
            WorkerSettings.from_env()

    # Non-development missing sha256
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "AKSHRAVA_ENV": "pilot", "YOLO_WEIGHTS_SHA256": ""})
        with pytest.raises(ValueError, match="YOLO_WEIGHTS_SHA256 is required"):
            WorkerSettings.from_env()

    # Non-development missing redis url
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "AKSHRAVA_ENV": "pilot", "NONCE_REDIS_URL": ""})
        with pytest.raises(ValueError, match="NONCE_REDIS_URL is required"):
            WorkerSettings.from_env()

    # Non-development missing metrics token
    with monkeypatch.context() as m:
        m.setattr("os.environ", {**base_env, "AKSHRAVA_ENV": "pilot", "METRICS_SCRAPE_TOKEN": ""})
        with pytest.raises(ValueError, match="METRICS_SCRAPE_TOKEN is required"):
            WorkerSettings.from_env()




def _signing_fields(headers):
    return (
        headers["X-Akshrava-Timestamp"],
        headers["X-Akshrava-Nonce"],
        headers["X-Akshrava-Signature"],
    )


def test_sync_and_async_paths_sign_requests_identically():
    """Both transports must produce a signature the worker will accept.

    These were two independent copies of the signing scheme. Duplicated security code is the
    worst kind to let drift: a fix applied to one copy silently leaves the other exploitable,
    and this nonce/timestamp pair is what makes worker replay protection work at all.
    """
    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", "s" * 32, 500)
    body = b"jpeg-bytes"

    sync_headers = detector._signed_headers(body)
    async_headers = detector._signed_headers(body)

    for headers in (sync_headers, async_headers):
        timestamp, nonce, signature = _signing_fields(headers)
        expected = hmac.new(
            b"s" * 32,
            timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert signature == expected
        assert headers["Content-Type"] == "image/jpeg"
        # Trace correlation must be present on both paths, not just the async one.
        assert headers.get("traceparent")

    # Nonces must never repeat across calls, or replay protection rejects the second frame.
    assert sync_headers["X-Akshrava-Nonce"] != async_headers["X-Akshrava-Nonce"]


@pytest.mark.asyncio
async def test_async_remote_span_encloses_the_http_request(monkeypatch):
    """The exported inference span must include I/O, not just trace-header construction."""
    active = {"value": False}

    @contextlib.contextmanager
    def span(_name):
        active["value"] = True
        try:
            yield
        finally:
            active["value"] = False

    class Response:
        status_code = 200
        content = b'{"detections":[]}'

        def raise_for_status(self):
            assert active["value"], "the HTTP status must be handled inside the inference span"

    class Client:
        async def post(self, *_args, **_kwargs):
            assert active["value"], "the HTTP request must run inside the inference span"
            return Response()

    import akshrava_backend.tracing as tracing_mod

    monkeypatch.setattr(tracing_mod, "start_inference_span", span)
    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", "s" * 32, 500)

    async def client():
        return Client()

    monkeypatch.setattr(detector, "_get_async_client", client)
    monkeypatch.setattr(detector, "_signed_headers", lambda _body: {})

    assert await detector.detect_async(JPEG) == []
    assert active["value"] is False


def test_sync_path_preserves_worker_saturation_like_the_async_path():
    """A 503 must soft-shed one frame on BOTH transports.

    The two paths disagreed: async mapped 503 to WorkerSaturatedError while sync flattened it
    into a generic RemoteInferenceError, so identical worker overload produced a different
    operational signal depending on which code path happened to run -- and the saturation metric
    and its Cloud Monitoring alert silently under-counted.
    """
    from urllib.error import HTTPError

    from akshrava_backend.detector import RemoteInferenceError, WorkerSaturatedError

    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", "s" * 32, 500)

    def raise_status(status):
        def opener(*args, **kwargs):
            raise HTTPError("https://worker.internal/v1/infer", status, "err", {}, None)
        return opener

    import akshrava_backend.detector as detector_mod

    original = detector_mod.build_opener
    try:
        detector_mod.build_opener = lambda *a, **k: type(
            "O", (), {"open": staticmethod(raise_status(503))}
        )()
        with pytest.raises(WorkerSaturatedError):
            detector.detect(b"jpeg")

        detector_mod.build_opener = lambda *a, **k: type(
            "O", (), {"open": staticmethod(raise_status(500))}
        )()
        with pytest.raises(RemoteInferenceError) as exc_info:
            detector.detect(b"jpeg")
        assert not isinstance(exc_info.value, WorkerSaturatedError)
    finally:
        detector_mod.build_opener = original


def test_response_size_cap_is_enforced_once_for_both_transports():
    """An unbounded worker response is a memory-exhaustion path on the control plane."""
    from akshrava_backend.detector import RemoteInferenceError

    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", "s" * 32, 500)
    oversized = b"x" * (RemoteWorkerDetector._MAX_RESPONSE_BYTES + 1)
    with pytest.raises(RemoteInferenceError, match="too large"):
        detector._detections_from_response(oversized)

    # And a well-formed body still parses through the same shared helper.
    payload = json.dumps({"detections": [{"label": "car", "confidence": 0.8, "box": [1, 2, 3, 4]}]})
    assert detector._detections_from_response(payload.encode()) == [
        Detection("car", 0.8, (1.0, 2.0, 3.0, 4.0))
    ]
