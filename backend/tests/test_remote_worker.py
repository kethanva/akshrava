import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from akshrava_backend.detector import Detector, FailoverRemoteWorkerDetector, RemoteWorkerDetector
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
    body = json.dumps({"image_b64": base64.b64encode(JPEG).decode("ascii")}).encode("utf-8")
    with TestClient(app) as client:
        assert client.post("/v1/infer", content=body).status_code == 401
        response = client.post("/v1/infer", content=body, headers=_signed_headers(body))
        assert response.status_code == 200
        assert response.json() == {
            "detections": [{"label": "person", "confidence": 0.9, "box": [1, 2, 3, 4]}]
        }
        assert client.post("/v1/infer", content=body, headers=_signed_headers(body)).status_code == 409


def test_gpu_worker_uses_detector_batch_contract():
    detector = BatchDetector()
    settings = WorkerSettings(SECRET, "unused.pt", 200_000, 1280, 30, require_gpu=False, batch_wait_ms=0)
    app = create_worker_app(settings, detector)
    body = json.dumps({"image_b64": base64.b64encode(JPEG).decode("ascii")}).encode("utf-8")
    with TestClient(app) as client:
        response = client.post("/v1/infer", content=body, headers=_signed_headers(body))
        assert response.status_code == 200
    assert detector.batch_sizes == [1]


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

    def fake_urlopen(request, timeout, context=None):
        captured["signature"] = request.headers["X-akshrava-signature"]
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("akshrava_backend.detector.urlopen", fake_urlopen)
    detector = RemoteWorkerDetector("https://worker.internal/v1/infer", SECRET, 450)
    assert detector.detect(JPEG) == [Detection("car", 0.8, (1.0, 2.0, 3.0, 4.0))]
    assert captured["signature"]
    assert captured["timeout"] == 0.45


def test_remote_detector_fails_over_to_warm_worker(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b'{"detections":[]}'

    def fake_urlopen(request, timeout, context=None):
        calls.append(request.full_url)
        if "one.internal" in request.full_url:
            from urllib.error import URLError
            raise URLError("preempted")
        return Response()

    monkeypatch.setattr("akshrava_backend.detector.urlopen", fake_urlopen)
    detector = FailoverRemoteWorkerDetector(
        ["https://one.internal/v1/infer", "https://two.internal/v1/infer"], SECRET, 450
    )
    assert detector.detect(JPEG) == []
    assert calls == ["https://one.internal/v1/infer", "https://two.internal/v1/infer"]
