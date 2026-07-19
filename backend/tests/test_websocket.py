import base64
import json
import asyncio
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from akshrava_backend import main
from akshrava_backend.application import SessionApplicationService
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


def test_revoked_device_is_closed_before_its_next_frame(monkeypatch):
    checks = 0

    async def revoked_after_handshake(_device_id):
        nonlocal checks
        checks += 1
        return checks > 1

    monkeypatch.setattr(main.store, "is_device_revoked", revoked_after_handshake)
    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_json(
                {
                    "type": "frame", "id": 1, "capture_mono_ms": 100, "w": 1, "h": 1,
                    "jpeg_bytes": len(JPEG), "camera_calibration_id": "test-r0",
                }
            )
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_json()
    assert disconnect.value.code == 4403


def test_metrics_endpoint_exposes_aggregate_operational_metrics():
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "akshrava_frames_processed_total" in response.text
    assert "akshrava_session_admission_rejected_total" in response.text
    assert "akshrava_frame_age_milliseconds_bucket" in response.text
    assert "akshrava_inference_duration_milliseconds_bucket" in response.text
    assert 'akshrava_pipeline_stage_duration_milliseconds_bucket{stage="decode"' in response.text


def test_metrics_observe_phone_supplied_frame_age_from_websocket_result():
    before = int(time.time() * 1000) - 120
    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "type": "frame",
                    "id": 77,
                    "capture_mono_ms": 100,
                    "capture_epoch_ms": before,
                    "w": 1,
                    "h": 1,
                    "jpeg_bytes": len(JPEG),
                    "camera_calibration_id": "test-r0",
                }
            )
            websocket.send_bytes(JPEG)
            assert websocket.receive_json()["type"] == "result"
            websocket.receive_json()
        metrics_text = client.get("/metrics").text
    assert "akshrava_frame_age_milliseconds_count" in metrics_text
    assert 'akshrava_frame_age_milliseconds_bucket{le="500"}' in metrics_text


def test_readiness_requires_a_usable_database_connection():
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_readiness_times_out_instead_of_hanging(monkeypatch):
    async def slow_ping():
        await asyncio.sleep(1)

    monkeypatch.setattr(main.store, "ping", slow_ping)
    monkeypatch.setattr(main, "settings", replace(main.settings, ready_timeout_ms=100))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503


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


def test_priority_look_includes_summary(tmp_path, monkeypatch):
    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            assert websocket.receive_json()["type"] == "ready"
            websocket.send_json({"type": "look"})
            assert websocket.receive_json()["type"] == "look_ack"
            websocket.send_json(
                {
                    "type": "frame",
                    "id": 9,
                    "capture_mono_ms": 300,
                    "w": 1,
                    "h": 1,
                    "jpeg_bytes": len(JPEG),
                    "camera_calibration_id": "test-r0",
                    "priority": True,
                    "mode": "priority",
                }
            )
            websocket.send_bytes(JPEG)
            result = websocket.receive_json()
            assert result["type"] == "result"
            assert result["priority"] is True
            assert result["look_summary"]
            assert "approach" not in result["look_summary"].lower()
            assert "safe" not in result["look_summary"].lower()
            assert websocket.receive_json()["type"] == "quality"


def test_priority_frames_get_their_own_bounded_rate_limit_not_an_unbounded_bypass():
    # Regression test: header.priority is client-asserted. Priority frames used to skip ALL
    # server-side rate limiting entirely, so any authenticated device (including a lost/stolen
    # kit still inside its token window) could flood the shared GPU at socket speed by stamping
    # priority=true on every frame. PRIORITY_FRAME_BURST=2.0 means the third rapid-fire priority
    # frame in the same instant must be rejected, not silently admitted.
    def priority_header(frame_id, capture_mono_ms):
        return {
            "type": "frame",
            "id": frame_id,
            "capture_mono_ms": capture_mono_ms,
            "w": 1,
            "h": 1,
            "jpeg_bytes": len(JPEG),
            "camera_calibration_id": "test-r0",
            "priority": True,
            "mode": "priority",
        }

    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token") as websocket:
            assert websocket.receive_json()["type"] == "ready"
            codes = []
            for index in range(4):
                websocket.send_json(priority_header(index + 1, 100 + index))
                websocket.send_bytes(JPEG)
                first = websocket.receive_json()
                if first["type"] == "error":
                    codes.append(first["code"])
                    continue
                assert first["type"] == "result"
                assert websocket.receive_json()["type"] == "quality"
            assert "frame_rate_limited" in codes, "an unbounded priority flood must eventually be rejected"


def test_inference_failure_explicitly_disables_vision(monkeypatch):
    class BrokenVision:
        async def analyze(self, state, header, jpeg):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(main, "vision", BrokenVision())
    monkeypatch.setattr(main, "session_application", SessionApplicationService(main.store, main.vision))
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


def test_websocket_consent_triggers_gcp_upload(monkeypatch):
    from unittest.mock import AsyncMock
    mock_upload = AsyncMock(return_value="http://storage/mock.jpg")
    monkeypatch.setattr(main.gcp_storage, "upload_frame", mock_upload)
    monkeypatch.setattr(main, "settings", replace(main.settings, gcp_diagnostics_bucket="test-bucket"))

    with TestClient(app) as client:
        with client.websocket_connect("/v1/session?token=dev-device-token&consent=true") as websocket:
            websocket.receive_json()
            websocket.send_json(
                {
                    "type": "frame",
                    "id": 1,
                    "capture_mono_ms": 100,
                    "w": 1,
                    "h": 1,
                    "jpeg_bytes": len(JPEG),
                    "camera_calibration_id": "test-r0",
                }
            )
            websocket.send_bytes(JPEG)
            assert websocket.receive_json()["type"] == "result"
            websocket.receive_json()  # consume quality message
            
            # Wait for background task to run
            time.sleep(0.1)

    mock_upload.assert_called_once()
    args, _ = mock_upload.call_args
    assert "dev-device" in args[0]
    assert args[1] == JPEG

