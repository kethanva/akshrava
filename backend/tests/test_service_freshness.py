"""Capture-to-receive age must count toward the speech freshness budget.

A frame that spent the uplink budget on the way in must not score (and therefore must not
spend an alert cooldown) even when inference itself was fast. The phone already drops that
speech via capture_mono_ms; scoring it anyway silenced the next timely detection.
"""

import pytest

from akshrava_backend.domain import FrameHeader, SessionState
from akshrava_backend.service import VisionService

from test_service import FixedPersonDetector, RecordingStore, SlowFixedPersonDetector


def _header(frame_id, capture_mono_ms, capture_epoch_ms=None):
    return FrameHeader(
        frame_id=frame_id,
        capture_mono_ms=capture_mono_ms,
        capture_epoch_ms=capture_epoch_ms,
        width=640,
        height=480,
        jpeg_bytes=1,
        calibration_id="test-r0",
        pitch_cdeg=-1200,
        roll_cdeg=0,
        pose_age_ms=20,
        mode="normal",
        priority=False,
    )


@pytest.mark.asyncio
async def test_uplink_age_counts_toward_the_freshness_budget():
    service = VisionService(FixedPersonDetector(), RecordingStore(), alert_max_age_ms=2_500)
    state = SessionState(device_id="device-1")
    received = 1_700_000_000_000
    await service.analyze(state, _header(1, 1_000, received - 100), b"jpeg", server_received_epoch_ms=received)
    late = await service.analyze(
        state,
        _header(2, 1_500, received - 3_000),
        b"jpeg",
        server_received_epoch_ms=received,
    )
    assert late["late_suppressed"] is True
    assert late["late_suppressed_reason"] == "capture_age"
    assert late["hazard"] is None
    assert late["capture_to_receive_ms"] == 3_000


@pytest.mark.asyncio
async def test_fast_inference_on_a_stale_frame_does_not_spend_an_alert_cooldown():
    service = VisionService(FixedPersonDetector(), RecordingStore(), alert_max_age_ms=2_500)
    state = SessionState(device_id="device-1")
    received = 1_700_000_000_000
    first = await service.analyze(
        state, _header(1, 1_000, received - 50), b"jpeg", server_received_epoch_ms=received
    )
    assert first["hazard"] is None
    stale = await service.analyze(
        state, _header(2, 1_500, received - 4_000), b"jpeg", server_received_epoch_ms=received
    )
    assert stale["late_suppressed"] is True
    assert stale["hazard"] is None
    assert state.last_alert_at_ms == {}
    timely = await service.analyze(
        state, _header(3, 2_000, received - 80), b"jpeg", server_received_epoch_ms=received
    )
    assert timely["late_suppressed"] is False
    assert timely["hazard"] is not None
    assert timely["hazard"]["message_key"] == "person_ahead"


@pytest.mark.asyncio
async def test_negative_clock_skew_falls_back_to_inference_only():
    service = VisionService(FixedPersonDetector(), RecordingStore(), alert_max_age_ms=2_500)
    state = SessionState(device_id="device-1")
    received = 1_700_000_000_000
    result = await service.analyze(
        state,
        _header(1, 1_000, received + 5_000),
        b"jpeg",
        server_received_epoch_ms=received,
    )
    assert result["capture_to_receive_ms"] is None
    assert result["late_suppressed"] is False


@pytest.mark.asyncio
async def test_capture_age_beyond_sixty_seconds_is_ignored_not_suppressive():
    service = VisionService(FixedPersonDetector(), RecordingStore(), alert_max_age_ms=2_500)
    state = SessionState(device_id="device-1")
    received = 1_700_000_000_000
    result = await service.analyze(
        state,
        _header(1, 1_000, received - 90_000),
        b"jpeg",
        server_received_epoch_ms=received,
    )
    assert result["capture_to_receive_ms"] is None
    assert result["late_suppressed"] is False


@pytest.mark.asyncio
async def test_late_suppressed_reason_distinguishes_uplink_from_compute():
    service = VisionService(FixedPersonDetector(), RecordingStore(), alert_max_age_ms=2_500)
    state = SessionState(device_id="device-1")
    received = 1_700_000_000_000
    uplink = await service.analyze(
        state, _header(1, 1_000, received - 3_000), b"jpeg", server_received_epoch_ms=received
    )
    assert uplink["late_suppressed_reason"] == "capture_age"
    slow = VisionService(SlowFixedPersonDetector(delay_s=0.05), RecordingStore(), alert_max_age_ms=10)
    compute = await slow.analyze(
        SessionState(device_id="device-2"),
        _header(1, 1_000, None),
        b"jpeg",
        server_received_epoch_ms=received,
    )
    assert compute["late_suppressed"] is True
    assert compute["late_suppressed_reason"] == "inference"
