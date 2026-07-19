import base64
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from akshrava_backend import main
from akshrava_backend.main import app


# A valid 1x1 JPEG. The noop detector is intentional in test/development mode.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/Aaf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/Aaf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Ap//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


def test_websocket_frame_round_trip():
    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            ready = websocket.receive_json()
            assert ready["type"] == "ready"
            assert ready["vision_enabled"] is False
            websocket.send_text(
                json.dumps(
                    {
                        "type": "frame",
                        "id": 7,
                        "capture_mono_ms": 100,
                        "w": 1,
                        "h": 1,
                        "jpeg_bytes": len(JPEG),
                        "camera_calibration_id": "test-r0",
                        "pitch_cdeg": -1000,
                        "roll_cdeg": 0,
                        "pose_age_ms": 10,
                    }
                )
            )
            websocket.send_bytes(JPEG)
            result = websocket.receive_json()
            assert result["type"] == "result"
            assert result["frame_id"] == 7
            assert result["hazard"] is None
            assert websocket.receive_json()["type"] == "quality"


def test_event_feed_requires_matching_device_token():
    with TestClient(app) as client:
        assert client.get("/v1/devices/dev-device/events").status_code == 401
        assert client.get(
            "/v1/devices/not-this-device/events",
            headers={"Authorization": "Bearer dev-device-token"},
        ).status_code == 403
        assert client.get(
            "/v1/devices/dev-device/events",
            headers={"Authorization": "Bearer dev-device-token"},
        ).status_code == 200


def test_metrics_endpoint_exposes_aggregate_operational_metrics():
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "akshrava_frames_processed_total" in response.text
    assert "akshrava_inference_duration_milliseconds_bucket" in response.text
    assert 'akshrava_pipeline_stage_duration_milliseconds_bucket{stage="decode"' in response.text


def test_readiness_requires_a_usable_database_connection():
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_websocket_rejects_non_monotonic_frame_headers():
    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            websocket.receive_json()
            header = {
                "type": "frame", "id": 1, "capture_mono_ms": 100, "w": 1, "h": 1,
                "jpeg_bytes": len(JPEG), "camera_calibration_id": "test-r0",
            }
            websocket.send_json(header)
            websocket.send_bytes(JPEG)
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_json({**header, "id": 2})
            assert websocket.receive_json()["code"] == "non_monotonic_capture"


def test_websocket_rejects_non_object_or_oversized_control_messages():
    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            websocket.receive_json()
            websocket.send_json(["not", "an", "object"])
            assert websocket.receive_json()["code"] == "protocol_error"
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()

        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            websocket.receive_json()
            websocket.send_text('{"type":"ping","padding":"' + ("x" * 4096) + '"}')
            assert websocket.receive_json()["code"] == "protocol_error"


def test_inference_failure_explicitly_disables_vision(monkeypatch):
    class BrokenVision:
        async def analyze(self, state, header, jpeg):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(main, "vision", BrokenVision())
    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "type": "frame", "id": 8, "capture_mono_ms": 200, "w": 1, "h": 1,
                    "jpeg_bytes": len(JPEG), "camera_calibration_id": "test-r0",
                }
            )
            websocket.send_bytes(JPEG)
            assert websocket.receive_json() == {"type": "error", "code": "vision_unavailable"}
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()
