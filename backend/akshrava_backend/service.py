import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Set

from .composer import hazard_payload, look_summary
from .alert_policy import AlertPolicy
from .detector import (
    Detector,
    RegistryRemoteWorkerDetector,
    RemoteWorkerDetector,
    TransientInferenceError,
)
from .domain import FrameHeader, SessionState
from .hazards import HazardScorer
from .tracker import SimpleTracker

logger = logging.getLogger(__name__)


class InferenceCircuitOpenError(RuntimeError):
    """Sustained inference failure for one device: the vision path is not usable right now.

    Distinct from TransientInferenceError. A single slow or failed frame is shed and the
    session continues; only a streak (_CIRCUIT_OPEN_AFTER) trips this, which is the point at
    which the phone must stop implying it can see and tell the user to use the cane or guide.
    """


class BackgroundTaskTracker:
    """Safely tracks and drains fire-and-forget background tasks.

    Prevents garbage collection mid-flight by holding strong references to running tasks,
    and logs exceptions raised during task execution to prevent silent failures.
    """

    def __init__(self, name: str):
        self.name = name
        self.tasks: Set[asyncio.Task] = set()

    def schedule(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def handle_done(t: asyncio.Task):
            self.tasks.discard(t)
            try:
                exc = t.exception()
                if exc is not None:
                    logger.error(
                        "Background task in %s failed: %s",
                        self.name,
                        exc,
                        exc_info=exc,
                    )
            except asyncio.CancelledError:
                pass
        task.add_done_callback(handle_done)

    async def drain(self, timeout: float = 2.0) -> None:
        if not self.tasks:
            return
        pending = list(self.tasks)
        done, still_pending = await asyncio.wait(pending, timeout=timeout)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)


class VisionService:
    """Phone-facing vision path.

    Track ID allocation is per-connection (`session_key`). Association state lives on
    `SessionState.tracks`; the helper only owns the next-ID counter so two concurrent
    phones never collide track IDs. Keys are connection-scoped so an old socket's cleanup
    cannot wipe a newer reconnect of the same device_id.
    """

    _POSE_DISCONTINUITY_CDEG = 1_200
    _CIRCUIT_OPEN_AFTER = 3
    _CIRCUIT_COOLDOWN_SECONDS = 5.0
    # Sweep expired per-device breaker entries once the dict grows past this, bounding memory
    # against device-id rotation without touching still-active circuits.
    _CIRCUIT_STATE_MAX = 10000

    def __init__(
        self,
        detector: Detector,
        store,
        alert_max_age_ms: int = 2500,
        language: str = "en",
        inference_timeout_ms: int = 800,
        inference_executor_workers: int = 2,
        tracker_factory: Callable[[], SimpleTracker] = SimpleTracker,
    ):
        self.detector = detector
        self.store = store
        self.language = language
        self._trackers: Dict[str, SimpleTracker] = {}
        self._tracker_factory = tracker_factory
        self.scorer = HazardScorer()
        self.alert_policy = AlertPolicy()
        self._inference_lock = asyncio.Lock()
        self.alert_max_age_ms = alert_max_age_ms
        self.inference_timeout_seconds = inference_timeout_ms / 1000.0
        self._inference_executor_workers = inference_executor_workers
        # Local YOLO and remote/cloud paths use separate pools so a hung cloud call cannot
        # starve local inference (and vice versa). asyncio.wait_for does not cancel the
        # underlying thread; isolation + a bounded worker count fail closed under saturation.
        self._local_executor = self._new_executor("akshrava-local-infer")
        self._remote_executor = self._new_executor("akshrava-remote-infer")
        # Per-device breakers: one hung phone/GPU path must not silence the fleet.
        self._timeout_streak: Dict[str, int] = {}
        self._circuit_open_until: Dict[str, float] = {}
        # Alert persistence / diagnostic uploads must never block the phone WebSocket reply.
        self._persist_tracker = BackgroundTaskTracker("alert-persistence")
        self._upload_tracker = BackgroundTaskTracker("diagnostic-uploads")

    def _new_executor(self, prefix: str) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self._inference_executor_workers,
            thread_name_prefix=prefix,
        )

    def _uses_remote_path(self) -> bool:
        return isinstance(self.detector, (RemoteWorkerDetector, RegistryRemoteWorkerDetector))

    def _tracker_key(self, state: SessionState) -> str:
        return state.session_key or state.device_id

    def _tracker(self, session_key: str) -> SimpleTracker:
        if session_key not in self._trackers:
            self._trackers[session_key] = self._tracker_factory()
        return self._trackers[session_key]

    async def analyze(self, state: SessionState, header: FrameHeader, jpeg: bytes) -> Dict:
        started = time.monotonic()
        detected_started = started
        # Local models and cloud-fallback wrappers retain mutable state. Remote workers opt in
        # to parallel operation explicitly; serializing every other detector prevents cross-phone
        # result attribution and model-runtime races. Noop/remote override requires_serial_execution.
        if self.detector.requires_serial_execution():
            async with self._inference_lock:
                detections, cloud_fallback_unavailable = await self._detect(state.device_id, jpeg)
        else:
            detections, cloud_fallback_unavailable = await self._detect(state.device_id, jpeg)
        detect_ms = int((time.monotonic() - detected_started) * 1000)
        track_score_started = time.monotonic()
        pose_discontinuity = self._pose_discontinuity(state, header)
        tracker = self._tracker(self._tracker_key(state))
        state.tracks = tracker.update(
            state.tracks,
            detections,
            discard_missed=pose_discontinuity,
        )
        self._remember_pose(state, header)

        inference_ms = int((time.monotonic() - started) * 1000)

        # Check the freshness budget BEFORE scoring, not after. The scorer mutates per-key and
        # per-device cooldown/rate-limit state as a side effect of producing a hazard (hazards.py
        # score()); scoring first and discarding the result afterward silently spends that budget
        # on a hazard nobody ever heard, so the next genuinely-timely detection of the same
        # object gets suppressed by a cooldown it never benefited from. Under sustained slow
        # inference this compounds into total silence. Skip scoring entirely once already late.
        late_suppressed = inference_ms > self.alert_max_age_ms
        is_priority = bool(header.priority) or header.mode == "priority"
        hazard = None
        if not late_suppressed:
            candidate = self.scorer.score(
                state,
                header.width,
                header.height,
                header.pose_age_ms,
                header.pitch_cdeg,
                header.roll_cdeg,
                state.geometry_profile,
                skip_cooldowns=is_priority,
            )
            hazard = self.alert_policy.admit(state, candidate, priority=is_priority)
        track_score_ms = int((time.monotonic() - track_score_started) * 1000)

        result = {
            "type": "result",
            "frame_id": header.frame_id,
            "capture_mono_ms": header.capture_mono_ms,
            "server_inference_ms": inference_ms,
            "server_received_epoch_ms": int(time.time() * 1000),
            "hazard": None,
            # Keep bounded detector telemetry in the protocol so a connected phone can be
            # distinguished from a healthy detector that simply saw no supported class.
            "detection_count": len(detections),
            "detection_labels": sorted({item.label for item in detections})[:20],
            "priority": is_priority,
            "look_summary": None,
            "late_suppressed": late_suppressed,
            "pipeline_stage_ms": {"detect": detect_ms, "track_score": track_score_ms},
        }
        # Language is a per-device provisioning setting (plan §6.2), not a fleet-wide server
        # default; state.language is set from the phone's own frame header (application.py).
        # self.language only covers a session that hasn't sent a header yet.
        language = state.language or self.language
        if cloud_fallback_unavailable is not None:
            result["cloud_fallback_unavailable"] = cloud_fallback_unavailable
        if hazard is not None:
            # Schedule persistence off the reply path so DB latency cannot delay a safety alert.
            result["pipeline_stage_ms"]["persist"] = 0
            self._schedule_record_alert(state.device_id, header.frame_id, hazard)
            result["hazard"] = hazard_payload(hazard, language)
        if is_priority:
            # Look answers even when late-suppressed (clear / delayed view) so the explicit
            # query is never silent; stale hazards are still not invented when late. But a
            # late-suppressed look never SCORED the frame at all (scoring is skipped above), so
            # hazard=None here means "we didn't check", not "we checked and it was clear" --
            # look_summary must say so rather than confidently claiming no hazard exists.
            result["look_summary"] = look_summary(hazard, language, checked=not late_suppressed)
        return result

    def _schedule_record_alert(self, device_id: str, frame_id: int, hazard) -> None:
        self._persist_tracker.schedule(self._record_alert_background(device_id, frame_id, hazard))

    def schedule_diagnostic_upload(self, coro) -> None:
        """Track diagnostic upload tasks so shutdown can drain them."""
        self._upload_tracker.schedule(coro)

    async def _record_alert_background(self, device_id: str, frame_id: int, hazard) -> None:
        try:
            await self.store.record_alert(device_id, frame_id, hazard)
        except Exception:
            logger.exception(
                "background alert persistence failed device_id=%s frame_id=%s",
                device_id,
                frame_id,
            )

    async def drain_persists(self, timeout: float = 2.0) -> None:
        """Wait for in-flight alert writes and diagnostic uploads (tests + graceful shutdown)."""
        await asyncio.gather(
            self._persist_tracker.drain(timeout=timeout),
            self._upload_tracker.drain(timeout=timeout),
            return_exceptions=True,
        )

    async def release_session(self, session_key: str) -> None:
        """Drop per-connection tracker state on disconnect/revocation."""
        self._trackers.pop(session_key, None)
        # Circuit keys are device-scoped; callers pass session_key — clear nothing here.
        # Device-level breaker clears on success; disconnect must not wipe another phone's state.

    def shutdown(self) -> None:
        # cancel_futures is Python 3.9+, while the package intentionally supports Python 3.8.
        # The bounded executor prevents unbounded queued work. Using wait=True ensures
        # all thread pools join during lifecycle teardowns and test runs.
        for tracker in (self._persist_tracker, self._upload_tracker):
            for task in list(tracker.tasks):
                task.cancel()
            tracker.tasks.clear()
        self._local_executor.shutdown(wait=True)
        self._remote_executor.shutdown(wait=True)
        # TestClient can start a fresh lifespan over the imported application. Recreate the
        # bounded pools lazily instead of retaining executors that have already been shut down.
        self._local_executor = self._new_executor("akshrava-local-infer")
        self._remote_executor = self._new_executor("akshrava-remote-infer")

    async def shutdown_async(self) -> None:
        await self.drain_persists()
        self.shutdown()
        close_method = getattr(self.detector, "close", None)
        if close_method is not None:
            if asyncio.iscoroutinefunction(close_method):
                await close_method()
            else:
                close_method()

    def _prune_circuit_state(self) -> None:
        """Drop expired circuit entries so device rotation cannot leak these dicts unbounded.

        release_session deliberately keeps per-device breaker state across a reconnect (an
        offending device should still find its circuit open). But a device that opens a circuit
        and never returns, or churns its id on re-provisioning, would otherwise leave an entry
        forever. An expired circuit (its cooldown has passed) is safe to forget; a device whose
        cooldown lapsed and reconnects simply starts a fresh streak. Cheap O(expired) sweep,
        only triggered once the dict grows past a threshold.
        """
        if len(self._circuit_open_until) <= self._CIRCUIT_STATE_MAX:
            return
        now = time.monotonic()
        expired = [key for key, until in self._circuit_open_until.items() if until <= now]
        for key in expired:
            self._circuit_open_until.pop(key, None)
            self._timeout_streak.pop(key, None)

    def _circuit_allows(self, device_id: str) -> None:
        """Raise if this device's circuit breaker is currently open."""
        until = self._circuit_open_until.get(device_id, 0.0)
        if time.monotonic() < until:
            raise InferenceCircuitOpenError("inference circuit open after repeated failures")

    def _note_failure(self, device_id: str, reason: str) -> None:
        """Count one failed frame, and open the breaker once the streak is sustained.

        Both deadlines and transient detector errors (worker unavailable, 5xx, malformed
        response) feed the same streak: from the phone's point of view they are the same
        condition — this frame produced nothing usable. Counting only timeouts would let a
        worker that fails fast every time shed frames forever without ever escalating.
        """
        streak = self._timeout_streak.get(device_id, 0) + 1
        self._timeout_streak[device_id] = streak
        if streak >= self._CIRCUIT_OPEN_AFTER:
            self._circuit_open_until[device_id] = time.monotonic() + self._CIRCUIT_COOLDOWN_SECONDS
            self._timeout_streak[device_id] = 0
            self._prune_circuit_state()
            logger.warning(
                "inference circuit opened for device=%s for %.1fs after %d consecutive failures (last=%s)",
                device_id,
                self._CIRCUIT_COOLDOWN_SECONDS,
                self._CIRCUIT_OPEN_AFTER,
                reason,
            )

    def _note_success(self, device_id: str) -> None:
        self._timeout_streak.pop(device_id, None)
        self._circuit_open_until.pop(device_id, None)

    async def _await_inference(self, device_id: str, awaitable):
        """Apply the inference deadline and keep the per-device breaker in step.

        A deadline or a transient detector error is re-raised as TransientInferenceError so the
        transport sheds just this frame. Either way the failure is counted, so a sustained
        outage still trips the breaker and escalates to a hard 'use cane or guide'.
        """
        try:
            result = await asyncio.wait_for(awaitable, timeout=self.inference_timeout_seconds)
        except asyncio.TimeoutError as exc:
            self._note_failure(device_id, "deadline")
            raise TransientInferenceError("inference deadline exceeded") from exc
        except TransientInferenceError as exc:
            self._note_failure(device_id, type(exc).__name__)
            raise
        self._note_success(device_id)
        return result

    async def _detect(self, device_id: str, jpeg: bytes):
        """Run detection with timeout + per-device circuit break.

        Remote HTTP adapters are preferred on the async path (request timeouts are killable).
        Local/sync detectors run on an isolated thread pool: wait_for cannot cancel a worker
        thread, so pool separation limits blast radius when one path hangs.
        """
        self._circuit_allows(device_id)
        loop = asyncio.get_running_loop()

        # Remote workers: async HTTP with follow_redirects=False; timeout is meaningful.
        if self._uses_remote_path():
            async_for_device = getattr(self.detector, "detect_async_for_device", None)
            call = (
                async_for_device(device_id, jpeg)
                if async_for_device is not None
                else self.detector.detect_async(jpeg)
            )
            return await self._await_inference(device_id, call), None

        # CloudFallbackDetector exposes provider + local; keep async for the availability bit.
        if getattr(self.detector, "provider", None) is not None and getattr(self.detector, "local", None) is not None:
            return await self._await_inference(
                device_id, self.detector.detect_async_with_status_for_device(device_id, jpeg)
            )

        # Local YOLO / noop: sync detect on the isolated local pool (not the default executor).
        device_method = getattr(self.detector, "detect_with_status_for_device", None)
        method = device_method or getattr(self.detector, "detect_with_status", None)
        if device_method is not None:
            def function(frame):
                return device_method(device_id, frame)
        elif method is not None:
            function = method
        else:
            def function(frame):
                return self.detector.detect_for_device(device_id, frame)
        result = await self._await_inference(
            device_id, loop.run_in_executor(self._local_executor, function, jpeg)
        )
        return result if method is not None else (result, None)

    def _pose_discontinuity(self, state: SessionState, header: FrameHeader) -> bool:
        if header.pose_age_ms is None or header.pose_age_ms > 100:
            return False
        if header.pitch_cdeg is None or header.roll_cdeg is None:
            return False
        if state.last_pitch_cdeg is None or state.last_roll_cdeg is None:
            return False
        return (
            abs(header.pitch_cdeg - state.last_pitch_cdeg) >= self._POSE_DISCONTINUITY_CDEG
            or abs(header.roll_cdeg - state.last_roll_cdeg) >= self._POSE_DISCONTINUITY_CDEG
        )

    @staticmethod
    def _remember_pose(state: SessionState, header: FrameHeader) -> None:
        if header.pose_age_ms is not None and header.pose_age_ms <= 100:
            if header.pitch_cdeg is not None and header.roll_cdeg is not None:
                state.last_pitch_cdeg = header.pitch_cdeg
                state.last_roll_cdeg = header.roll_cdeg
