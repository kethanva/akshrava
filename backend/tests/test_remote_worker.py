import asyncio
import base64
import hashlib
import hmac
import json
import time

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

