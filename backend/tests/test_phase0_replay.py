"""Phase-0 policy replay: exercise hazard/composer contracts on synthetic detections."""

import json
from pathlib import Path

import pytest

from akshrava_backend.composer import hazard_payload
from akshrava_backend.detector import Detector
from akshrava_backend.domain import Detection, FrameHeader, Hazard, SessionState
from akshrava_backend.service import VisionService

FIXTURE = Path(__file__).resolve().parents[2] / "datasets" / "phase0" / "events.json"


class ScriptedDetector(Detector):
    def __init__(self, frames):
        self._frames = frames
        self._index = 0

    def detect(self, jpeg):
        frame = self._frames[min(self._index, len(self._frames) - 1)]
        self._index += 1
        return list(frame)


class RecordingStore:
    def __init__(self):
        self.alerts = []

    async def record_alert(self, device_id, frame_id, hazard):
        self.alerts.append((device_id, frame_id, hazard))


def _header(frame_id, capture_mono_ms):
    return FrameHeader(
        frame_id=frame_id,
        capture_mono_ms=capture_mono_ms,
        capture_epoch_ms=None,
        width=640,
        height=480,
        jpeg_bytes=1,
        calibration_id="phase0-r0",
        pitch_cdeg=-1200,
        roll_cdeg=0,
        pose_age_ms=20,
        mode="normal",
        priority=False,
    )


def _expand_events(raw_events):
    """Build >=50 synthetic frames by repeating core fixtures with unique frame ids."""
    frames = []
    expectations = []
    while len(frames) < 50:
        for event in raw_events:
            detection = Detection(
                label=event["label"],
                confidence=float(event["confidence"]),
                box=tuple(event["box"]),
            )
            frames.append([detection])
            expectations.append(event)
            if len(frames) >= 50:
                break
    return frames, expectations


@pytest.mark.asyncio
async def test_phase0_policy_replay_enforces_safety_contract():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    frames, expectations = _expand_events(payload["events"])
    assert len(frames) >= 50

    store = RecordingStore()
    service = VisionService(ScriptedDetector(frames), store, alert_max_age_ms=10_000)
    state = SessionState(device_id="phase0-device")
    spoken = 0

    try:
        for index, event in enumerate(expectations):
            result = await service.analyze(state, _header(index + 1, 1_000 + index * 500), b"jpeg")
            assert "frame_id" in result
            assert "server_inference_ms" in result
            hazard = result.get("hazard")
            if event.get("expect_silent"):
                continue
            if hazard is not None:
                spoken += 1
                assert hazard.get("motion_evidence") == "insufficient"
                blob = json.dumps(hazard).lower()
                assert "approach" not in blob
                assert "safe to cross" not in blob
                key = hazard.get("message_key")
                assert key in {"obstacle_ahead", "vehicle_nearby", "busy_road"}
        await service.drain_persists()
    finally:
        service.shutdown()

    assert spoken >= 1
    sample = hazard_payload(
        Hazard(
            kind="obstacle",
            level="caution",
            bearing="ahead",
            confidence=0.9,
            severity="S2",
            range_band="near",
            range_valid=False,
            message_key="obstacle_ahead",
            haptic="double",
            track_id=1,
        ),
        "en",
    )
    assert sample["motion_evidence"] == "insufficient"
