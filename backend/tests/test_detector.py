import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from akshrava_backend.detector import (
    Detector,
    NoopDetector,
    RemoteWorkerDetector,
    RemoteInferenceError,
    WorkerSaturatedError,
    _RejectRedirectHandler,
    StaticInferenceEndpointRegistry,
    RegistryRemoteWorkerDetector,
    UltralyticsDetector,
    make_detector,
    jpeg_dimensions,
)
from akshrava_backend.domain import Detection
from akshrava_backend.model_integrity import verify_model_sha256


# ============================================================================
# 1. Detector Base Class & Helper Functions
# ============================================================================

class DummyDetector(Detector):
    def detect(self, jpeg: bytes) -> list[Detection]:
        return [Detection("person", 0.9, (10.0, 10.0, 50.0, 50.0))]


@pytest.mark.asyncio
async def test_detector_base_class_default_methods(valid_jpeg_bytes):
    dummy = DummyDetector()
    assert dummy.requires_serial_execution() is True
    
    batches = dummy.detect_batch([valid_jpeg_bytes, valid_jpeg_bytes])
    assert len(batches) == 2
    assert len(batches[0]) == 1

    assert len(dummy.detect_for_device("dev1", valid_jpeg_bytes)) == 1

    dets_async = await dummy.detect_async(valid_jpeg_bytes)
    assert len(dets_async) == 1

    dets_async_dev = await dummy.detect_async_for_device("dev1", valid_jpeg_bytes)
    assert len(dets_async_dev) == 1

    dets_status, fallback = await dummy.detect_async_with_status_for_device("dev1", valid_jpeg_bytes)
    assert len(dets_status) == 1
    assert fallback is False


def test_jpeg_dimensions_validation(valid_jpeg_bytes):
    dims = jpeg_dimensions(valid_jpeg_bytes)
    assert dims == (640, 480)

    with pytest.raises(ValueError, match="invalid JPEG"):
        jpeg_dimensions(b"not-a-jpeg-image")


def test_noop_detector(valid_jpeg_bytes):
    noop = NoopDetector()
    assert noop.detect(valid_jpeg_bytes) == []
    assert noop.requires_serial_execution() is False


def test_reject_redirect_handler():
    handler = _RejectRedirectHandler()
    with pytest.raises(RemoteInferenceError, match="redirect rejected"):
        handler.redirect_request(None, None, 302, "Found", None, "http://newurl")


# ============================================================================
# 2. RemoteWorkerDetector Tests
# ============================================================================

def test_remote_worker_detector_init_validation():
    with pytest.raises(ValueError, match="must include scheme and host"):
        RemoteWorkerDetector(endpoint="not-a-url", shared_secret="secret123", timeout_ms=500)

    with pytest.raises(ValueError, match="host is not on the allowlist"):
        RemoteWorkerDetector(
            endpoint="http://worker.internal/v1/infer",
            shared_secret="secret123",
            timeout_ms=500,
            allowed_hosts={"other.host"},
        )


def test_remote_worker_detector_parse_detection_validation():
    # Invalid non-dict
    with pytest.raises(ValueError, match="must be an object"):
        RemoteWorkerDetector._parse_detection("not-a-dict")
    # Invalid label
    with pytest.raises(ValueError, match="invalid label"):
        RemoteWorkerDetector._parse_detection({"label": 123, "confidence": 0.5, "box": [0, 0, 10, 10]})
    # Invalid confidence type
    with pytest.raises(ValueError, match="invalid confidence"):
        RemoteWorkerDetector._parse_detection({"label": "car", "confidence": True, "box": [0, 0, 10, 10]})
    # Out of range confidence
    with pytest.raises(ValueError, match="confidence out of range"):
        RemoteWorkerDetector._parse_detection({"label": "car", "confidence": 1.5, "box": [0, 0, 10, 10]})
    # Invalid box
    with pytest.raises(ValueError, match="invalid box"):
        RemoteWorkerDetector._parse_detection({"label": "car", "confidence": 0.8, "box": [0, 0, 10]})
    # Invalid box value type
    with pytest.raises(ValueError, match="invalid box value"):
        RemoteWorkerDetector._parse_detection({"label": "car", "confidence": 0.8, "box": [0, 0, "10", 10]})
    # Invalid box order (min > max)
    with pytest.raises(ValueError, match="invalid box order"):
        RemoteWorkerDetector._parse_detection({"label": "car", "confidence": 0.8, "box": [50, 50, 10, 10]})


@pytest.mark.asyncio
async def test_remote_worker_detector_async_detect(valid_jpeg_bytes):
    detector = RemoteWorkerDetector(
        endpoint="http://127.0.0.1:8090/v1/infer",
        shared_secret="test-secret-32-bytes-long-for-hmac-security-key",
        timeout_ms=500,
    )

    # 1. Normal response
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = json.dumps({
        "detections": [{"label": "person", "confidence": 0.9, "box": [10.0, 20.0, 30.0, 40.0]}]
    }).encode("utf-8")
    mock_client.post.return_value = mock_resp
    detector._async_client = mock_client

    dets = await detector.detect_async(valid_jpeg_bytes)
    assert len(dets) == 1
    assert dets[0].label == "person"

    # 2. Worker Saturated (503)
    mock_resp_503 = MagicMock()
    mock_resp_503.status_code = 503
    mock_client.post.return_value = mock_resp_503

    with pytest.raises(WorkerSaturatedError):
        await detector.detect_async(valid_jpeg_bytes)

    # 3. Redirect / HTTP status error
    import httpx
    err_resp = MagicMock()
    err_resp.status_code = 302
    mock_client.post.side_effect = httpx.HTTPStatusError("Redirect", request=MagicMock(), response=err_resp)

    with pytest.raises(RemoteInferenceError, match="redirect rejected"):
        await detector.detect_async(valid_jpeg_bytes)

    # 4. Response too large (>256KB)
    mock_client.post.side_effect = None
    large_resp = MagicMock()
    large_resp.status_code = 200
    large_resp.content = b"x" * 300_000
    mock_client.post.return_value = large_resp

    with pytest.raises(RemoteInferenceError, match="response too large"):
        await detector.detect_async(valid_jpeg_bytes)

    # 5. Invalid JSON response
    invalid_resp = MagicMock()
    invalid_resp.status_code = 200
    invalid_resp.content = b"not-json"
    mock_client.post.return_value = invalid_resp

    with pytest.raises(RemoteInferenceError, match="invalid remote worker response"):
        await detector.detect_async(valid_jpeg_bytes)

    await detector.close()


# ============================================================================
# 3. StaticInferenceEndpointRegistry & RegistryRemoteWorkerDetector
# ============================================================================

def test_static_inference_endpoint_registry():
    # from_urls
    reg = StaticInferenceEndpointRegistry.from_urls(["http://w1.local:8090", "http://w2.local:8090"])
    assert len(reg.endpoints) == 2
    assert reg.allowed_hosts() == {"w1.local", "w2.local"}

    # empty urls raises ValueError
    with pytest.raises(ValueError, match="at least one enabled inference endpoint"):
        StaticInferenceEndpointRegistry([])

    # from_json valid
    json_str = json.dumps([
        {"id": "w1", "url": "http://w1.local:8090", "enabled": True},
        {"id": "w2", "url": "http://w2.local:8090", "enabled": False},
    ])
    reg_json = StaticInferenceEndpointRegistry.from_json(json_str)
    assert len(reg_json.endpoints) == 1
    assert reg_json.endpoints[0].id == "w1"

    # from_json invalid
    with pytest.raises(ValueError, match="must be valid JSON"):
        StaticInferenceEndpointRegistry.from_json("invalid-json")
    with pytest.raises(ValueError, match="must be a list"):
        StaticInferenceEndpointRegistry.from_json("{}")
    with pytest.raises(ValueError, match="entries require id and url"):
        StaticInferenceEndpointRegistry.from_json('[{"id": ""}]')

    # ordered_for_device determinism
    ordered1 = reg.ordered_for_device("dev-123")
    ordered2 = reg.ordered_for_device("dev-123")
    assert ordered1 == ordered2


@pytest.mark.asyncio
async def test_registry_remote_worker_detector(valid_jpeg_bytes):
    reg = StaticInferenceEndpointRegistry.from_urls(["http://w1.local:8090", "http://w2.local:8090"])
    registry_detector = RegistryRemoteWorkerDetector(
        registry=reg,
        shared_secret="secret123secret123secret12312345",
        timeout_ms=500,
    )
    assert registry_detector.requires_serial_execution() is False

    # Mock workers detect_async
    for worker in registry_detector._workers.values():
        worker.detect_async = AsyncMock(return_value=[Detection("car", 0.85, (0.0, 0.0, 10.0, 10.0))])

    dets = await registry_detector.detect_async(valid_jpeg_bytes)
    assert len(dets) == 1
    assert dets[0].label == "car"

    # Test error aggregation: all WorkerSaturatedError
    for worker in registry_detector._workers.values():
        worker.detect_async = AsyncMock(side_effect=WorkerSaturatedError("queue full"))

    with pytest.raises(WorkerSaturatedError, match="all configured remote workers are saturated"):
        await registry_detector.detect_async_for_device("dev1", valid_jpeg_bytes)

    # Test error aggregation: mixed errors
    first_worker = next(iter(registry_detector._workers.values()))
    first_worker.detect_async = AsyncMock(side_effect=RemoteInferenceError("unavailable"))
    with pytest.raises(RemoteInferenceError, match="all configured remote workers are unavailable"):
        await registry_detector.detect_async_for_device("dev1", valid_jpeg_bytes)

    # Aggregate helper empty errors
    assert isinstance(RegistryRemoteWorkerDetector._aggregate_remote_errors([]), RemoteInferenceError)

    await registry_detector.close()


# ============================================================================
# 4. UltralyticsDetector & Model Integrity Tests
# ============================================================================

def test_yolo_detector_refuses_missing_weights_before_import_or_download():
    with pytest.raises(RuntimeError, match="local.*model file"):
        UltralyticsDetector("/definitely/not/a/model.pt")


def test_model_sha256_gate_rejects_missing_or_mismatched_hash(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"approved model fixture\n")

    with pytest.raises(RuntimeError, match="required"):
        verify_model_sha256(str(model), "", required=True)
    with pytest.raises(RuntimeError, match="does not match"):
        verify_model_sha256(str(model), "0" * 64, required=True)


def test_model_sha256_gate_accepts_matching_hash(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"approved model fixture\n")
    expected = "355a37c17b79eaa4c8b50c1bfd988eabf0ca077d22598232b4afd9d85235c7ba"

    assert verify_model_sha256(str(model), expected, required=True) == expected


@pytest.mark.asyncio
async def test_ultralytics_detector_predict_mock(tmp_path, valid_jpeg_bytes):
    model_file = tmp_path / "yolo.pt"
    model_file.write_bytes(b"fake yolo weights")

    mock_yolo_cls = MagicMock()
    mock_model = MagicMock()
    mock_yolo_cls.return_value = mock_model

    box_mock = MagicMock()
    xyxy_item = MagicMock()
    xyxy_item.tolist.return_value = [10.0, 20.0, 100.0, 200.0]
    box_mock.xyxy = [xyxy_item]
    box_mock.conf = [MagicMock(item=lambda: 0.88)]
    box_mock.cls = [MagicMock(item=lambda: 0)]

    result_mock = MagicMock()
    result_mock.names = {0: "person"}
    result_mock.boxes = [box_mock]

    mock_model.predict.return_value = [result_mock]

    with patch.dict("sys.modules", {"ultralytics": MagicMock(YOLO=mock_yolo_cls)}):
        detector = UltralyticsDetector(weights=str(model_file))
        dets = detector.detect(valid_jpeg_bytes)
        assert len(dets) == 1
        assert dets[0].label == "person"

        dets_async = await detector.detect_async(valid_jpeg_bytes)
        assert len(dets_async) == 1

        dets_dev_async = await detector.detect_async_for_device("dev1", valid_jpeg_bytes)
        assert len(dets_dev_async) == 1

        detector.close()


def test_ultralytics_detector_missing_import(tmp_path):
    model_file = tmp_path / "yolo.pt"
    model_file.write_bytes(b"fake yolo weights")
    with patch.dict("sys.modules", {"ultralytics": None}):
        with pytest.raises(RuntimeError, match="install backend\\[yolo\\]"):
            UltralyticsDetector(weights=str(model_file))


# ============================================================================
# 5. make_detector Factory Tests
# ============================================================================

def test_make_detector_factory():
    # noop
    d_noop = make_detector("noop", "")
    assert isinstance(d_noop, NoopDetector)

    # remote
    d_remote = make_detector(
        "remote",
        "",
        remote_inference_url="http://127.0.0.1:8090",
        remote_worker_secret="secret123secret123secret12312345",
    )
    assert isinstance(d_remote, RegistryRemoteWorkerDetector)

    # unknown kind
    with pytest.raises(RuntimeError, match="unknown DETECTOR=invalid"):
        make_detector("invalid", "")
