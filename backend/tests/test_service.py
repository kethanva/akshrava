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


class DeviceAwareDetector(Detector):
    def __init__(self):
        self.device_ids = []

    def detect(self, jpeg):
        raise AssertionError("VisionService should pass device id to device-aware detectors")

    def detect_for_device(self, device_id, jpeg):
        self.device_ids.append(device_id)
        return []


class RecordingStore:
    def __init__(self):
        self.alerts = []

    async def record_alert(self, device_id, frame_id, hazard):
        self.alerts.append((device_id, frame_id, hazard))


class SlowRecordingStore(RecordingStore):
    def __init__(self, delay_s=0.05):
        super().__init__()
        self.delay_s = delay_s
        self.started = asyncio.Event()

    async def record_alert(self, device_id, frame_id, hazard):
        self.started.set()
        await asyncio.sleep(self.delay_s)
        await super().record_alert(device_id, frame_id, hazard)


def _header(frame_id, capture_mono_ms, priority=False, mode="normal"):
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
        mode=mode,
        priority=priority,
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
    assert second["pipeline_stage_ms"]["persist"] == 0
    await service.drain_persists()
    assert store.alerts[0][0:2] == ("device-1", 2)


@pytest.mark.asyncio
async def test_alert_persistence_does_not_block_websocket_result():
    store = SlowRecordingStore(delay_s=0.08)
    service = VisionService(FixedPersonDetector(), store, alert_max_age_ms=1_000)
    state = SessionState(device_id="device-1")
    await service.analyze(state, _header(1, 1_000), b"jpeg")
    started = time.monotonic()
    second = await service.analyze(state, _header(2, 1_500), b"jpeg")
    elapsed = time.monotonic() - started
    assert second["hazard"] is not None
    assert elapsed < 0.05, "DB write must not sit on the phone reply path"
    # Yield so the scheduled persist task can enter record_alert before we assert.
    await asyncio.sleep(0)
    assert store.started.is_set()
    await service.drain_persists()
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
async def test_vision_service_passes_device_id_to_device_aware_detector():
    detector = DeviceAwareDetector()
    service = VisionService(detector, RecordingStore())
    await service.analyze(SessionState(device_id="pilot-phone-1"), _header(1, 1_000), b"jpeg")
    assert detector.device_ids == ["pilot-phone-1"]


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


@pytest.mark.asyncio
async def test_per_session_trackers_do_not_share_id_counters():
    service = VisionService(EmptyDetector(), RecordingStore())
    a = service._tracker("device-a")
    b = service._tracker("device-b")
    assert a is not b
    from akshrava_backend.domain import Detection

    a.update([], [Detection("person", 0.9, (0, 0, 10, 10))])
    b.update([], [Detection("person", 0.9, (0, 0, 10, 10))])
    assert a._next_id == 2
    assert b._next_id == 2


@pytest.mark.asyncio
async def test_release_session_does_not_wipe_a_newer_reconnect_tracker():
    # Old socket cleanup keyed by device_id used to pop the tracker that a newer session
    # for the same phone had already started using.
    service = VisionService(EmptyDetector(), RecordingStore())
    state_old = SessionState(device_id="phone-1", session_key="conn-old")
    state_new = SessionState(device_id="phone-1", session_key="conn-new")
    await service.analyze(state_old, _header(1, 1_000), b"")
    await service.analyze(state_new, _header(1, 1_000), b"")
    old_tracker = service._tracker("conn-old")
    new_tracker = service._tracker("conn-new")
    assert old_tracker is not new_tracker
    await service.release_session("conn-old")
    assert "conn-old" not in service._trackers
    assert service._tracker("conn-new") is new_tracker


@pytest.mark.asyncio
async def test_priority_look_bypasses_cooldown_and_returns_look_summary():
    service = VisionService(FixedPersonDetector(), RecordingStore(), language="en")
    state = SessionState(device_id="look-device")
    first = await service.analyze(state, _header(1, 1_000), b"jpeg")
    second = await service.analyze(state, _header(2, 1_500), b"jpeg")
    assert second["hazard"] is not None
    blocked = await service.analyze(state, _header(3, 2_000), b"jpeg")
    assert blocked["hazard"] is None
    look = await service.analyze(
        state, _header(4, 2_500, priority=True, mode="priority"), b"jpeg"
    )
    assert look["priority"] is True
    assert look["hazard"] is not None
    assert look["look_summary"]
    assert "approach" not in look["look_summary"].lower()
    assert "safe" not in look["look_summary"].lower()
    assert first["look_summary"] is None


@pytest.mark.asyncio
async def test_late_suppressed_priority_look_says_unchecked_not_clear():
    # Regression test: a look answered while the server is behind its freshness budget must not
    # reassure the user that the view was empty — the frame was never scored (late_suppressed).
    service = VisionService(SlowFixedPersonDetector(delay_s=0.02), RecordingStore(), alert_max_age_ms=1)
    state = SessionState(device_id="device-1")
    look = await service.analyze(state, _header(1, 1_000, priority=True, mode="priority"), b"jpeg")
    assert look["late_suppressed"] is True
    assert look["hazard"] is None
    assert "clear" not in look["look_summary"].lower()
    assert "could not" in look["look_summary"].lower() or "try again" in look["look_summary"].lower()


@pytest.mark.asyncio
async def test_spoken_output_uses_the_devices_own_provisioned_language():
    # Regression test: VisionService.language used to be one fleet-wide value shared by every
    # connected device (plan §6.2 -- language is a per-device provisioning setting). A phone
    # whose session is provisioned in Hindi must get Hindi speech even though the server's own
    # constructor default is English. (application.py sets state.language from the phone's own
    # frame header -- see test_application.py; this pins VisionService's side of the contract.)
    service = VisionService(FixedPersonDetector(), RecordingStore(), language="en")
    state = SessionState(device_id="device-1", language="hi")
    await service.analyze(state, _header(1, 1_000), b"jpeg")
    second = await service.analyze(state, _header(2, 1_500), b"jpeg")
    assert second["hazard"] is not None
    assert "आगे" in second["hazard"]["spoken_preview"] or "रुकावट" in second["hazard"]["spoken_preview"]


@pytest.mark.asyncio
async def test_inference_circuit_is_per_device():
    class SlowDetector:
        def requires_serial_execution(self):
            return False

        def detect_for_device(self, device_id, jpeg):
            import time as _time
            _time.sleep(0.05)
            return []

    class PeerDetector:
        def requires_serial_execution(self):
            return False

        def detect_for_device(self, device_id, jpeg):
            return []

    service = VisionService(
        SlowDetector(),
        RecordingStore(),
        inference_timeout_ms=10,
        inference_executor_workers=2,
    )
    service._CIRCUIT_OPEN_AFTER = 2
    service._CIRCUIT_COOLDOWN_SECONDS = 30.0
    for _ in range(2):
        with pytest.raises(RuntimeError, match="deadline exceeded"):
            await service._detect("bad-phone", b"jpeg")
    with pytest.raises(RuntimeError, match="circuit open"):
        await service._detect("bad-phone", b"jpeg")
    service.detector = PeerDetector()
    service.inference_timeout_seconds = 1.0
    detections, status = await service._detect("good-phone", b"jpeg")
    assert detections == []
    assert status is None


@pytest.mark.asyncio
async def test_circuit_breaker_state_is_bounded_against_device_rotation(monkeypatch):
    # release_session keeps per-device breaker state across reconnects by design, but a device
    # that opens a circuit and never returns (or rotates its id) must not leak these dicts
    # forever. Once past the cap, expired circuits are swept.
    service = VisionService(FixedPersonDetector(), RecordingStore())
    monkeypatch.setattr(type(service), "_CIRCUIT_STATE_MAX", 5)
    # Open circuits for many devices with an already-past cooldown, then trigger one more open
    # to fire the prune. Directly drive the internal breaker to avoid needing real timeouts.
    for index in range(20):
        service._circuit_open_until["dead-%d" % index] = 0.0  # already expired
    service._timeout_streak["live"] = service._CIRCUIT_OPEN_AFTER - 1
    service._note_timeout("live")  # crosses threshold -> opens + prunes
    try:
        assert len(service._circuit_open_until) <= 6  # <= cap + the freshly opened "live"
        assert "live" in service._circuit_open_until  # active circuit preserved
    finally:
        service.shutdown()
