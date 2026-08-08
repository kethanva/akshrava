import asyncio
import time

import pytest

from akshrava_backend.detector import Detector, TransientInferenceError
from akshrava_backend.domain import Detection, FrameHeader, SessionState
from akshrava_backend.service import VisionService


class FixedPersonDetector(Detector):
    def detect(self, jpeg):
        return [Detection(label="person", confidence=0.9, box=(220, 100, 430, 460))]


class FixedCarDetector(Detector):
    def detect(self, jpeg):
        return [Detection(label="car", confidence=0.9, box=(220, 100, 430, 460))]


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
    # exact `person_ahead` key consumed by AlertManager on Android.
    first = await service.analyze(state, _header(1, 1_000), b"jpeg")
    assert first["detection_count"] == 1
    assert first["detection_labels"] == ["person"]
    second = await service.analyze(state, _header(2, 1_500), b"jpeg")

    assert first["hazard"] is None
    assert second["hazard"]["message_key"] == "person_ahead"
    assert second["hazard"]["range_valid"] is False
    assert second["pipeline_stage_ms"]["persist"] == 0
    await service.drain_persists()
    assert store.alerts[0][0:2] == ("device-1", 2)


@pytest.mark.asyncio
async def test_timely_vehicle_detection_reports_telemetry_and_conservative_s2_without_range_claims():
    service = VisionService(FixedCarDetector(), RecordingStore(), alert_max_age_ms=1_000)
    state = SessionState(device_id="vehicle-device")

    first = await service.analyze(state, _header(1, 1_000), b"jpeg")
    second = await service.analyze(state, _header(2, 1_500), b"jpeg")

    assert first["detection_count"] == 1
    assert first["detection_labels"] == ["car"]
    assert second["late_suppressed"] is False
    assert second["hazard"]["message_key"] == "vehicle_nearby"
    assert second["hazard"]["level"] == "caution"
    assert second["hazard"]["range_valid"] is False


@pytest.mark.asyncio
async def test_shared_alert_budget_scores_under_limit_and_keeps_labels_when_late():
    # CPU remote pilot uses ALERT_MAX_AGE_MS=8500 so multi-second YOLO can still score.
    # Slow-but-under-budget inference must still speak; over-budget keeps detector telemetry.
    store = RecordingStore()
    detector = SlowFixedPersonDetector(delay_s=0.05)
    service = VisionService(detector, store, alert_max_age_ms=8_500)
    state = SessionState(device_id="cpu-pilot")

    await service.analyze(state, _header(1, 1_000), b"jpeg")
    under_budget = await service.analyze(state, _header(2, 1_500), b"jpeg")
    assert under_budget["late_suppressed"] is False
    assert under_budget["hazard"]["message_key"] == "person_ahead"
    assert under_budget["detection_labels"] == ["person"]

    service.alert_max_age_ms = 1
    late = await service.analyze(state, _header(3, 2_000), b"jpeg")
    assert late["late_suppressed"] is True
    assert late["hazard"] is None
    assert late["detection_count"] == 1
    assert late["detection_labels"] == ["person"]


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
    assert elapsed < 0.1, "DB write must not sit on the phone reply path"
    # Yield so the scheduled persist task can enter record_alert before we assert.
    await asyncio.sleep(0.02)
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
    assert second["detection_count"] == 1
    assert second["detection_labels"] == ["person"]
    assert state.last_alert_at_ms == {}, "a hazard suppressed for lateness must not reserve a cooldown slot"

    # Frame 3: inference speed recovers. The object is still tracked (hits=3) and on time --
    # it must fire immediately, not be silenced by a cooldown frame 2 should never have set.
    service.alert_max_age_ms = 10_000
    third = await service.analyze(state, _header(3, 2_000), b"jpeg")
    assert third["late_suppressed"] is False
    assert third["hazard"] is not None
    assert third["hazard"]["message_key"] == "person_ahead"


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
    # These now subclass Detector rather than being bare duck-typed objects. The service used to
    # discover how to call its detector by probing for optional attributes, so anything with the
    # right method names worked; it now goes through the declared port, which is the point --
    # a detector that does not implement the contract fails loudly here instead of silently
    # taking an unintended dispatch path in production.
    class SlowDetector(Detector):
        def requires_serial_execution(self):
            return False

        def detect(self, jpeg):
            return self.detect_for_device("", jpeg)

        def detect_for_device(self, device_id, jpeg):
            import time as _time
            _time.sleep(0.05)
            return []

    class PeerDetector(Detector):
        def requires_serial_execution(self):
            return False

        def detect(self, jpeg):
            return []

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
    service._note_failure("live", "deadline")  # crosses threshold -> opens + prunes
    try:
        assert len(service._circuit_open_until) <= 6  # <= cap + the freshly opened "live"
        assert "live" in service._circuit_open_until  # active circuit preserved
    finally:
        service.shutdown()


@pytest.mark.asyncio
async def test_transient_detector_error_is_shed_but_still_trips_the_breaker():
    """A worker that fails fast must escalate, not shed frames forever.

    Only timeouts used to feed the breaker, so a worker returning 5xx on every frame stayed
    "transient" indefinitely and the phone kept streaming into a vision path that never
    produced a result.
    """
    from akshrava_backend.detector import RemoteInferenceError
    from akshrava_backend.service import InferenceCircuitOpenError

    class FailingDetector(Detector):
        def requires_serial_execution(self):
            return False

        def detect(self, jpeg):
            raise RemoteInferenceError("remote worker unavailable")

        def detect_for_device(self, device_id, jpeg):
            return self.detect(jpeg)

    service = VisionService(FailingDetector(), RecordingStore(), inference_executor_workers=1)
    service._CIRCUIT_OPEN_AFTER = 2
    service._CIRCUIT_COOLDOWN_SECONDS = 30.0
    try:
        for _ in range(2):
            with pytest.raises(TransientInferenceError):
                await service._detect("bad-phone", b"jpeg")
        # Streak reached: the breaker escalates to a distinct, non-transient failure.
        with pytest.raises(InferenceCircuitOpenError):
            await service._detect("bad-phone", b"jpeg")
    finally:
        service.shutdown()


@pytest.mark.asyncio
async def test_inference_deadline_is_transient_not_a_session_ending_failure():
    from akshrava_backend.service import InferenceCircuitOpenError

    class SlowDetector(Detector):
        def requires_serial_execution(self):
            return False

        def detect(self, jpeg):
            time.sleep(0.05)
            return []

        def detect_for_device(self, device_id, jpeg):
            return self.detect(jpeg)

    service = VisionService(
        SlowDetector(), RecordingStore(), inference_timeout_ms=10, inference_executor_workers=1
    )
    try:
        with pytest.raises(TransientInferenceError):
            await service._detect("slow-phone", b"jpeg")
        # A deadline is explicitly NOT the circuit-open escalation.
        assert not isinstance(TransientInferenceError("x"), InferenceCircuitOpenError)
    finally:
        service.shutdown()


async def test_cancel_all_stops_tracked_work_that_has_nowhere_left_to_go():
    """Work whose destination is gone must be cancelled, and the caller must know it has unwound.

    A frame still being analysed when its WebSocket dies can only fail on the send, and it holds
    a reference to session state the connection teardown is about to release. cancel_all() must
    therefore both cancel and await, so nothing is mid-await on that state afterwards.
    """
    from akshrava_backend.service import BackgroundTaskTracker

    started = asyncio.Event()
    cancelled = asyncio.Event()
    completed = False

    async def long_running():
        nonlocal completed
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        completed = True

    tracker = BackgroundTaskTracker("test-cancel")
    tracker.schedule(long_running())
    await started.wait()
    await tracker.cancel_all()

    assert cancelled.is_set()
    assert completed is False
    assert all(task.done() for task in tracker.tasks)


async def test_cancel_all_is_safe_when_nothing_is_in_flight():
    from akshrava_backend.service import BackgroundTaskTracker

    await BackgroundTaskTracker("test-empty").cancel_all()


@pytest.mark.asyncio
async def test_a_new_detector_needs_no_change_to_visionservice():
    """The OCP property this port exists for.

    VisionService used to branch on `isinstance(detector, RemoteWorkerDetector)` and probe five
    optional attributes, so the application core knew every adapter by name and adding one meant
    editing the service. This detector type did not exist when _detect was written; it must work
    anyway, purely by declaring how it wants to be driven.
    """
    from akshrava_backend.detector import INFERENCE_MODE_ASYNC, InferenceOutcome

    class BrandNewAsyncDetector(Detector):
        def requires_serial_execution(self):
            return False

        def detect(self, jpeg):
            raise AssertionError("async adapters must not be driven through the sync path")

        def inference_mode(self):
            return INFERENCE_MODE_ASYNC

        async def infer_async(self, device_id, jpeg):
            return InferenceOutcome([Detection("person", 0.9, (0.0, 0.0, 1.0, 1.0))], None)

    service = VisionService(BrandNewAsyncDetector(), RecordingStore())
    detections, unavailable = await service._detect("device-1", b"jpeg")
    assert [item.label for item in detections] == ["person"]
    assert unavailable is None


@pytest.mark.asyncio
async def test_sync_detectors_run_off_the_event_loop_on_the_bounded_pool():
    """A blocking detector must never execute on the event loop.

    If it did, one slow model call would stall every other session on the instance -- not just
    the phone that sent the frame. The bound on the pool is what makes a hung model fail closed
    instead of consuming every worker.
    """
    import threading

    loop_thread = threading.current_thread().name
    seen = {}

    class BlockingDetector(Detector):
        def requires_serial_execution(self):
            return False

        def detect(self, jpeg):
            seen["thread"] = threading.current_thread().name
            return []

    service = VisionService(BlockingDetector(), RecordingStore(), inference_executor_workers=2)
    await service._detect("device-1", b"jpeg")
    assert seen["thread"] != loop_thread
    assert seen["thread"].startswith("akshrava-local-infer")


@pytest.mark.asyncio
async def test_cloud_fallback_availability_is_per_frame_not_per_detector():
    """Two phones sharing one detector must not read each other's vendor-outage bit."""
    from akshrava_backend.detector import INFERENCE_MODE_ASYNC, InferenceOutcome

    class PerFrameFallbackDetector(Detector):
        def __init__(self):
            self.calls = 0

        def requires_serial_execution(self):
            return False

        def detect(self, jpeg):
            return []

        def inference_mode(self):
            return INFERENCE_MODE_ASYNC

        async def infer_async(self, device_id, jpeg):
            self.calls += 1
            # Vendor is down for the first frame only.
            return InferenceOutcome([], self.calls == 1)

    service = VisionService(PerFrameFallbackDetector(), RecordingStore())
    _first, first_unavailable = await service._detect("phone-a", b"jpeg")
    _second, second_unavailable = await service._detect("phone-b", b"jpeg")
    assert first_unavailable is True
    assert second_unavailable is False


def test_visionservice_does_not_import_concrete_detector_adapters():
    """Guard the dependency direction: the core must not know its adapters by name.

    A re-introduced isinstance check here is how the OCP/DIP violation grows back.
    """
    import ast
    import inspect

    import akshrava_backend.service as service_mod

    tree = ast.parse(inspect.getsource(service_mod))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(alias.name for alias in node.names)

    for adapter in ("RemoteWorkerDetector", "RegistryRemoteWorkerDetector", "CloudFallbackDetector"):
        assert adapter not in imported, (
            "service.py imports the concrete adapter %s; the core must depend on the port only"
            % adapter
        )

    # Parse rather than grep so the assertion cannot be satisfied or broken by prose in a comment
    # or docstring -- only by real code.
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    for call in calls:
        if isinstance(call.func, ast.Name) and call.func.id == "isinstance":
            source_of_args = ast.dump(call)
            for adapter in ("RemoteWorkerDetector", "RegistryRemoteWorkerDetector"):
                assert adapter not in source_of_args, (
                    "service.py dispatches on concrete adapter %s" % adapter
                )
        # No probing the detector for optional methods to decide how to call it.
        if isinstance(call.func, ast.Name) and call.func.id == "getattr" and call.args:
            target = call.args[0]
            is_detector = (
                isinstance(target, ast.Attribute)
                and target.attr == "detector"
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            )
            if is_detector and len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
                assert not str(call.args[1].value).startswith("detect"), (
                    "service.py probes self.detector for %r instead of using the port"
                    % call.args[1].value
                )
