"""
Akshrava Backend Pytest Shared Fixtures & Mocks.

Safety Boundary: Object / vehicle awareness ONLY. Zero navigation, crossing,
collision, approach-speed, clear-path, or "safe" wording/logic.
"""

import io
import time
from typing import Any
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from akshrava_backend.detector import RemoteWorkerDetector
from akshrava_backend.storage import Store, Device, CalibrationProfileRecord


# ============================================================================
# 1. JPEG & Image Utility Fixtures
# ============================================================================

@pytest.fixture
def valid_jpeg_bytes() -> bytes:
    """Generates a minimal valid 640x480 RGB JPEG byte string in memory."""
    img = Image.new("RGB", (640, 480), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def valid_frame_header(valid_jpeg_bytes: bytes) -> dict[str, Any]:
    """Returns a valid text frame header dictionary."""
    return {
        "type": "frame",
        "frame_id": 100,
        "capture_mono_ms": 10000,
        "jpeg_bytes": len(valid_jpeg_bytes),
        "width": 640,
        "height": 480,
        "priority": False,
    }


# ============================================================================
# 1b. Process-wide Settings Patching
# ============================================================================

@pytest.fixture
def patched_settings():
    """Temporarily override fields on the app's frozen, process-wide Settings singleton.

    Settings is `frozen=True`, so overriding needs object.__setattr__. Doing that inline in a
    test leaks whenever an assertion fails first: the app keeps running with, say,
    environment="pilot", and unrelated later tests fail for reasons that have nothing to do with
    what they cover. This fixture always restores, pass or fail.
    """
    from akshrava_backend.main import settings as app_settings

    originals: dict[str, Any] = {}

    def _apply(**overrides: Any) -> None:
        for field, value in overrides.items():
            if not hasattr(app_settings, field):
                raise AttributeError(f"Settings has no field {field!r}")
            originals.setdefault(field, getattr(app_settings, field))
            object.__setattr__(app_settings, field, value)

    try:
        yield _apply
    finally:
        for field, value in originals.items():
            object.__setattr__(app_settings, field, value)


# ============================================================================
# 2. Database / SQLAlchemy Store Fixtures
# ============================================================================

@pytest.fixture
async def test_sqlite_store() -> AsyncGenerator[Store, None]:
    """Provides an isolated, in-memory SQLite Store instance."""
    store = Store("sqlite+aiosqlite:///:memory:", bootstrap_schema=True)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
async def provisioned_device(test_sqlite_store: Store) -> Device:
    """Pre-populates test_sqlite_store with a registered test device and profile."""
    device_id = "test-device-001"
    device = Device(
        device_id=device_id,
    )
    CalibrationProfileRecord(
        calibration_id="r0",
        focal_px=520.0,
        camera_height_m=1.3,
        reference_height_px=480,
        verified=True,
    )
    await test_sqlite_store.upsert_device(device_id, "r0")
    await test_sqlite_store.upsert_calibration_profile("r0", 520.0, 1.3, verified=True)
    return device



# ============================================================================
# 3. Redis In-Memory Mock Fixtures
# ============================================================================

class MockAsyncRedis:
    """In-memory async mock client mimicking redis.asyncio.Redis."""

    def __init__(self):
        self._data: dict[str, Any] = {}
        self._ttls: dict[str, float] = {}
        self._sorted_sets: dict[str, dict[str, float]] = {}

    async def get(self, key: str) -> Any | None:
        return self._data.get(key)

    async def set(
        self, key: str, value: Any, ex: int | None = None, nx: bool = False
    ) -> bool:
        if nx and key in self._data:
            return False
        self._data[key] = value
        if ex:
            self._ttls[key] = time.time() + ex
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                count += 1
            if k in self._sorted_sets:
                del self._sorted_sets[k]
                count += 1
        return count

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        if key not in self._sorted_sets:
            self._sorted_sets[key] = {}
        added = 0
        for member, score in mapping.items():
            if member not in self._sorted_sets[key]:
                added += 1
            self._sorted_sets[key][member] = score
        return added

    async def zrem(self, key: str, *members: str) -> int:
        if key not in self._sorted_sets:
            return 0
        count = 0
        for m in members:
            if m in self._sorted_sets[key]:
                del self._sorted_sets[key][m]
                count += 1
        return count

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> int:
        if key not in self._sorted_sets:
            return 0
        to_del = [
            m for m, s in self._sorted_sets[key].items()
            if min_score <= s <= max_score
        ]
        for m in to_del:
            del self._sorted_sets[key][m]
        return len(to_del)

    async def eval(self, script: str, numkeys: int, *keys_and_args) -> Any:
        # Generic fallback script runner returning success
        return 1

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


@pytest.fixture
def mock_redis_client() -> MockAsyncRedis:
    """Fixture returning an isolated MockAsyncRedis instance."""
    return MockAsyncRedis()


@pytest.fixture
def patch_redis_from_url(mock_redis_client: MockAsyncRedis):
    """Patches akshrava_backend.redis_util.async_redis_from_url to return mock_redis_client."""
    with patch(
        "akshrava_backend.redis_util.async_redis_from_url",
        return_value=mock_redis_client,
    ):
        yield mock_redis_client


# ============================================================================
# 4. GCP Remote Worker Fixtures
# ============================================================================

@pytest.fixture
def mock_gcp_detection_payload() -> dict[str, Any]:
    """Structured mock YOLO worker JSON response payload."""
    return {
        "detections": [
            {
                "label": "person",
                "confidence": 0.88,
                "box": [10.0, 20.0, 100.0, 200.0],
            },
            {
                "label": "car",
                "confidence": 0.75,
                "box": [200.0, 100.0, 400.0, 250.0],
            },
        ]
    }


@pytest.fixture
def mock_httpx_remote_worker(mock_gcp_detection_payload: dict[str, Any]):
    """Helper to mock httpx.AsyncClient responses for GCP remote worker."""
    def _create_mock_client(
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        exception: Exception | None = None,
    ):
        mock_client = MagicMock(spec=httpx.AsyncClient)
        if exception:
            mock_client.post = AsyncMock(side_effect=exception)
        else:
            resp_payload = payload if payload is not None else mock_gcp_detection_payload
            mock_resp = MagicMock(spec=httpx.Response)
            mock_resp.status_code = status_code
            mock_resp.json.return_value = resp_payload
            mock_resp.raise_for_status.side_effect = (
                httpx.HTTPStatusError(
                    message=f"HTTP {status_code}",
                    request=MagicMock(),
                    response=mock_resp,
                ) if status_code >= 400 else None
            )
            mock_client.post = AsyncMock(return_value=mock_resp)
        return mock_client

    return _create_mock_client


@pytest.fixture
def mock_remote_worker_detector(
    mock_gcp_detection_payload: dict[str, Any],
    mock_httpx_remote_worker,
) -> RemoteWorkerDetector:
    """Instantiates a RemoteWorkerDetector using a mocked httpx client."""
    client = mock_httpx_remote_worker(status_code=200, payload=mock_gcp_detection_payload)
    detector = RemoteWorkerDetector(
        endpoint="http://127.0.0.1:8090/v1/infer",
        hmac_secret="test-secret-32-bytes-long-for-hmac-security-key",
        timeout_seconds=2.0,
    )
    detector._client = client
    return detector


# ============================================================================
# 5. Cloud Fallback Vision SDK Mock Fixtures (AWS, GCP, Azure)
# ============================================================================

@pytest.fixture
def mock_aws_rekognition_client():
    """Mock boto3 client for AWS Rekognition detect_labels."""
    mock_client = MagicMock()
    mock_client.detect_labels.return_value = {
        "Labels": [
            {
                "Name": "Person",
                "Confidence": 92.5,
                "Instances": [
                    {
                        "BoundingBox": {
                            "Left": 0.1,
                            "Top": 0.2,
                            "Width": 0.3,
                            "Height": 0.5,
                        },
                        "Confidence": 92.5,
                    }
                ],
            },
            {
                "Name": "Car",
                "Confidence": 85.0,
                "Instances": [
                    {
                        "BoundingBox": {
                            "Left": 0.5,
                            "Top": 0.3,
                            "Width": 0.4,
                            "Height": 0.4,
                        },
                        "Confidence": 85.0,
                    }
                ],
            },
        ]
    }
    return mock_client


@pytest.fixture
def mock_gcp_vision_client():
    """Mock google-cloud-vision ImageAnnotatorClient."""
    mock_client = MagicMock()

    obj1 = MagicMock()
    obj1.name = "Person"
    obj1.score = 0.91
    obj1.bounding_poly.normalized_vertices = [
        MagicMock(x=0.1, y=0.2),
        MagicMock(x=0.4, y=0.2),
        MagicMock(x=0.4, y=0.7),
        MagicMock(x=0.1, y=0.7),
    ]

    obj2 = MagicMock()
    obj2.name = "Car"
    obj2.score = 0.82
    obj2.bounding_poly.normalized_vertices = [
        MagicMock(x=0.5, y=0.3),
        MagicMock(x=0.9, y=0.3),
        MagicMock(x=0.9, y=0.7),
        MagicMock(x=0.5, y=0.7),
    ]

    response = MagicMock()
    response.localized_object_annotations = [obj1, obj2]
    mock_client.object_localization.return_value = response
    return mock_client


@pytest.fixture
def mock_azure_vision_client():
    """Mock Azure ImageAnalysisClient analyze."""
    mock_client = MagicMock()

    obj1 = MagicMock()
    obj1.tags = [MagicMock(name="person", confidence=0.89)]
    obj1.bounding_box = MagicMock(x=64, y=96, width=192, height=240)

    result = MagicMock()
    result.objects.list = [obj1]
    mock_client.analyze.return_value = result
    return mock_client


# ============================================================================
# 6. WebSocket Session Mock Client
# ============================================================================

class MockWebSocket:
    """Mock FastAPI WebSocket object for session testing."""

    def __init__(self):
        self.sent_json: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.is_closed = False

    async def accept(self):
        pass

    async def send_json(self, data: dict[str, Any]):
        if self.is_closed:
            raise RuntimeError("WebSocket closed")
        self.sent_json.append(data)

    async def send_bytes(self, data: bytes):
        if self.is_closed:
            raise RuntimeError("WebSocket closed")
        self.sent_bytes.append(data)

    async def close(self, code: int = 1000, reason: str = ""):
        self.is_closed = True
        self.close_code = code
        self.close_reason = reason


@pytest.fixture
def mock_websocket() -> MockWebSocket:
    """Fixture providing an instance of MockWebSocket."""
    return MockWebSocket()
