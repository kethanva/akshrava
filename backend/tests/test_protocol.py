import pytest

from akshrava_backend.protocol import ProtocolError, parse_frame_header, quality_for_inference


def test_parses_valid_frame_header():
    header = parse_frame_header(
        {
            "type": "frame",
            "id": 1,
            "capture_mono_ms": 42,
            "capture_epoch_ms": 123,
            "w": 640,
            "h": 480,
            "jpeg_bytes": 1200,
            "camera_calibration_id": "device-r0",
            "pitch_cdeg": -1200,
            "roll_cdeg": 30,
            "pose_age_ms": 10,
        }
    )
    assert header.frame_id == 1
    assert header.pitch_cdeg == -1200


def test_rejects_invalid_frame_header():
    with pytest.raises(ProtocolError):
        parse_frame_header({"type": "frame", "id": -1})


def test_quality_sheds_capture_cost_when_server_work_uses_freshness_budget():
    assert quality_for_inference(100) == {"type": "quality", "max_side": 640, "jpeg_q": 60, "fps": 1.0}
    assert quality_for_inference(200) == {"type": "quality", "max_side": 512, "jpeg_q": 45, "fps": 0.7}
    assert quality_for_inference(400) == {"type": "quality", "max_side": 384, "jpeg_q": 35, "fps": 0.5}
