import time
import asyncio

import pytest

from akshrava_backend.detector import Detector
from akshrava_backend.domain import Detection, FrameHeader, SessionState
from akshrava_backend.service import VisionService


class FixedPersonDetector(Detector):
    def detect(self, jpeg):
        return [Detection(label="person", confidence=0.9, box=(220, 100, 430, 460))]


class SlowFixedPersonDetector(Detector):
    def __init__(self, delay_s=0.01):
        self.delay_s = delay_s

    def detect(self, jpeg):
        time.sleep(self.delay_s)
        return [Detection(label="person", confidence=0.9, box=(220, 100, 430, 460))]


class ParallelSlowDetector(SlowFixedPersonDetector):
    def requires_serial_execution(self):
        return False


class EmptyDetector(Detector):
    def detect(self, jpeg):
        return []


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
        calibration_id="test-r0",
        pitch_cdeg=-1200,
        roll_cdeg=0,
        pose_age_ms=20,
        mode="normal",
    )


@pytest.mark.asyncio
async def test_detector_to_hazard_result_uses_the_phone_audio_message_contract():
    store = RecordingStore()
    service = VisionService(FixedPersonDetector(), store)
    state = SessionState(device_id="device-1")

    # Persistence requires two observations; the first stays silent, the second reaches the
    # exact `obstacle_ahead` key consumed by AlertManager on Android.
    first = await service.analyze(state, _header(1, 1_000), b"jpeg")
    second = await service.analyze(state, _header(2, 1_500), b"jpeg")

    assert first["hazard"] is None
    assert second["hazard"]["message_key"] == "obstacle_ahead"
    assert second["hazard"]["range_valid"] is False
    assert store.alerts[0][0:2] == ("device-1", 2)


@pytest.mark.asyncio
async def test_late_inference_never_consumes_the_alert_cooldown():
    # Regression test: the hazard scorer used to run (and mutate per-key cooldown state) BEFORE
    # the freshness check, so a hazard discarded for arriving late had already spent the
    # cooldown that the very next, genuinely on-time detection of the same object needed --
    # compounding into silence under sustained slow inference. Scoring must be skipped entirely
    # once a frame is already late, not scored-then-discarded.
    store = RecordingStore()
    detector = SlowFixedPersonDetector(delay_s=0.01)
    service = VisionService(detector, store, alert_max_age_ms=10_000)
    state = SessionState(device_id="device-1")

    # Frame 1: first observation; tracker persistence not yet satisfied (hits=1) -> silent.
    first = await service.analyze(state, _header(1, 1_000), b"jpeg")
    assert first["hazard"] is None

    # Frame 2: second observation would normally fire (hits=2) -- force it late.
    service.alert_max_age_ms = 1
    second = await service.analyze(state, _header(2, 1_500), b"jpeg")
    assert second["late_suppressed"] is True
    assert second["hazard"] is None
    assert state.last_alert_at_ms == {}, "a hazard suppressed for lateness must not reserve a cooldown slot"

    # Frame 3: inference speed recovers. The object is still tracked (hits=3) and on time --
    # it must fire immediately, not be silenced by a cooldown frame 2 should never have set.
    service.alert_max_age_ms = 10_000
    third = await service.analyze(state, _header(3, 2_000), b"jpeg")
    assert third["late_suppressed"] is False
    assert third["hazard"] is not None
    assert third["hazard"]["message_key"] == "obstacle_ahead"


@pytest.mark.asyncio
async def test_remote_safe_detector_requests_do_not_wait_on_an_unrelated_phone():
    # A remote worker signs and handles each request independently; serializing at the control
    # plane would otherwise turn one slow phone into latency for every other phone.
    service = VisionService(ParallelSlowDetector(delay_s=0.08), RecordingStore(), alert_max_age_ms=1_000)
    started = time.monotonic()
    await asyncio.gather(
        service.analyze(SessionState(device_id="device-1"), _header(1, 1_000), b"jpeg"),
        service.analyze(SessionState(device_id="device-2"), _header(1, 1_000), b"jpeg"),
    )
    assert time.monotonic() - started < 0.14


@pytest.mark.asyncio
async def test_fresh_large_pose_jump_drops_unmatched_stale_tracks():
    service = VisionService(FixedPersonDetector(), RecordingStore(), alert_max_age_ms=1_000)
    state = SessionState(device_id="device-1")
    await service.analyze(state, _header(1, 1_000), b"jpeg")
    assert len(state.tracks) == 1

    # No observation after a large fresh rotation: retaining the old image-space box would be
    # false persistence. The new pose is remembered while the stale track is discarded.
    service.detector = EmptyDetector()
    moved = FrameHeader(
        frame_id=2, capture_mono_ms=1_500, capture_epoch_ms=None, width=640, height=480,
        jpeg_bytes=1, calibration_id="test-r0", pitch_cdeg=1_300, roll_cdeg=0,
        pose_age_ms=20, mode="normal",
    )
    await service.analyze(state, moved, b"")
    assert state.tracks == []
