import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock

from .alert_policy import AlertPolicy
from .composer import hazard_payload, look_summary
from .detector import (
    INFERENCE_MODE_ASYNC,
    Detector,
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
        self.tasks: set[asyncio.Task] = set()

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
        _done, still_pending = await asyncio.wait(pending, timeout=timeout)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)

    async def cancel_all(self) -> None:
        """Cancel every tracked task and wait for it to unwind.

        Unlike drain(), this gives no grace period: it is for work whose destination is already
        gone (a closed WebSocket), where finishing would only raise on the send. Awaiting the
        cancellations still matters -- it guarantees no task is mid-await on shared session state
        when the caller tears that state down.
        """
        if not self.tasks:
            return
        pending = list(self.tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


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
        self._trackers: dict[str, SimpleTracker] = {}
        self._tracker_factory = tracker_factory
        self.scorer = HazardScorer()
        self.alert_policy = AlertPolicy()
        self._inference_lock = asyncio.Lock()
        self.alert_max_age_ms = alert_max_age_ms
        self.inference_timeout_seconds = inference_timeout_ms / 1000.0
        self._inference_executor_workers = inference_executor_workers
        # One bounded pool for blocking (sync-mode) detectors. ThreadPoolExecutor's worker count
        # does NOT bound its internal submission queue, so a separate non-blocking admission gate
        # is required: timed-out calls keep their slot until the underlying thread really exits.
        # A hung model can therefore occupy at most this many threads and zero queued frames.
        self._local_executor = self._new_executor("akshrava-local-infer")
        self._local_inference_slots = BoundedSemaphore(self._inference_executor_workers)
        self._local_inference_gate_lock = Lock()
        self._local_inference_keys: set[tuple[str, str]] = set()
        # Per-device breakers: one hung phone/GPU path must not silence the fleet.
        self._timeout_streak: dict[str, int] = {}
        self._circuit_open_until: dict[str, float] = {}
        # Alert persistence / diagnostic uploads must never block the phone WebSocket reply.
        self._persist_tracker = BackgroundTaskTracker("alert-persistence")
        self._upload_tracker = BackgroundTaskTracker("diagnostic-uploads")

    def _new_executor(self, prefix: str) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self._inference_executor_workers,
            thread_name_prefix=prefix,
        )

    def _tracker_key(self, state: SessionState) -> str:
        return state.session_key or state.device_id

    def _tracker(self, session_key: str) -> SimpleTracker:
        if session_key not in self._trackers:
            self._trackers[session_key] = self._tracker_factory()
        return self._trackers[session_key]

    async def analyze(
        self,
        state: SessionState,
        header: FrameHeader,
        jpeg: bytes,
        *,
        server_received_epoch_ms: int | None = None,
    ) -> dict:
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
        received_epoch_ms = server_received_epoch_ms if server_received_epoch_ms is not None else int(time.time() * 1000)
        capture_to_receive_ms = None
        if header.capture_epoch_ms is not None:
            delta = received_epoch_ms - header.capture_epoch_ms
            # capture_epoch_ms is phone wall-clock and therefore untrusted. Same bound main.py
            # already uses for the age histogram; skew falls back to inference-only rather than
            # suppressing every frame on a phone with a wrong clock.
            if 0 <= delta <= 60_000:
                capture_to_receive_ms = delta
        effective_age_ms = inference_ms + (capture_to_receive_ms or 0)

        # Check the freshness budget BEFORE scoring, not after. The scorer mutates per-key and
        # per-device cooldown/rate-limit state as a side effect of producing a hazard (hazards.py
        # score()); scoring first and discarding the result afterward silently spends that budget
        # on a hazard nobody ever heard, so the next genuinely-timely detection of the same
        # object gets suppressed by a cooldown it never benefited from. Under sustained slow
        # inference this compounds into total silence. Skip scoring entirely once already late.
        late_suppressed = effective_age_ms > self.alert_max_age_ms
        late_suppressed_reason = None
        if late_suppressed:
            if inference_ms > self.alert_max_age_ms:
                late_suppressed_reason = "inference"
            else:
                late_suppressed_reason = "capture_age"
        is_priority = bool(header.priority) or header.mode == "priority"
        hazard = None
        drop_reason = None
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
            hazard, drop_reason = self.alert_policy.admit_with_reason(
                state, candidate, priority=is_priority
            )
        track_score_ms = int((time.monotonic() - track_score_started) * 1000)

        result = {
            "type": "result",
            "frame_id": header.frame_id,
            "capture_mono_ms": header.capture_mono_ms,
            "server_inference_ms": inference_ms,
            "server_received_epoch_ms": received_epoch_ms,
            "capture_to_receive_ms": capture_to_receive_ms,
            "hazard": None,
            # Keep bounded detector telemetry in the protocol so a connected phone can be
            # distinguished from a healthy detector that simply saw no supported class.
            "detection_count": len(detections),
            "detection_labels": sorted({item.label for item in detections})[:20],
            "priority": is_priority,
            "look_summary": None,
            "late_suppressed": late_suppressed,
            "late_suppressed_reason": late_suppressed_reason,
            "alert_suppressed_reason": drop_reason,
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
        # cancel_futures is available on the supported Python 3.10+ runtime.
        # The admission gate prevents unbounded queued work; tracked async tasks are cancelled.
        for tracker in (self._persist_tracker, self._upload_tracker):
            for task in list(tracker.tasks):
                task.cancel()
            tracker.tasks.clear()
        # A detector thread may be permanently wedged; graceful shutdown must not wait forever.
        # cancel_futures drops anything not yet running (normally none because admission slots
        # prevent queueing). Running threads retain their captured old semaphore until they exit.
        self._local_executor.shutdown(wait=False, cancel_futures=True)
        # TestClient can start a fresh lifespan over the imported application. Recreate the
        # bounded pool lazily instead of retaining an executor that has already been shut down.
        self._local_executor = self._new_executor("akshrava-local-infer")
        self._local_inference_slots = BoundedSemaphore(self._inference_executor_workers)
        self._local_inference_gate_lock = Lock()
        self._local_inference_keys = set()

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
        now = time.monotonic()
        if len(self._circuit_open_until) > self._CIRCUIT_STATE_MAX:
            expired = [key for key, until in self._circuit_open_until.items() if until <= now]
            for key in expired:
                self._circuit_open_until.pop(key, None)
                self._timeout_streak.pop(key, None)
        if len(self._timeout_streak) > self._CIRCUIT_STATE_MAX:
            active_circuits = set(self._circuit_open_until)
            stale = [k for k in self._timeout_streak if k not in active_circuits]
            for k in stale:
                self._timeout_streak.pop(k, None)

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
        """Run detection with a deadline and the per-device circuit breaker.

        The adapter *declares* how it wants to be driven (`inference_mode`) instead of this method
        inspecting concrete adapter types or optional methods. There are exactly two ways to call
        a detector and the detector picks one.

        Async adapters are awaited directly, because their deadline is real -- an httpx request
        can actually be abandoned. Sync adapters go to a bounded thread pool, because
        asyncio.wait_for cannot cancel a running thread; bounding the pool is what stops a hung
        model from consuming every worker and silencing the whole instance.
        """
        self._circuit_allows(device_id)

        if self.detector.inference_mode() == INFERENCE_MODE_ASYNC:
            outcome = await self._await_inference(
                device_id, self.detector.infer_async(device_id, jpeg)
            )
        else:
            loop = asyncio.get_running_loop()
            slots = self._local_inference_slots
            gate_lock = self._local_inference_gate_lock
            in_flight_keys = self._local_inference_keys
            # A serial detector keeps one global key until its underlying call truly finishes;
            # stateless parallel detectors keep one key per device so a single timed-out phone
            # cannot occupy every worker before its circuit breaker opens.
            inference_key = (
                ("serial", "")
                if self.detector.requires_serial_execution()
                else ("device", device_id)
            )
            with gate_lock:
                admitted = inference_key not in in_flight_keys and slots.acquire(blocking=False)
                if admitted:
                    in_flight_keys.add(inference_key)
            if not admitted:
                self._note_failure(device_id, "local_executor_saturated")
                raise TransientInferenceError("local inference executor saturated")

            def release_admission() -> None:
                with gate_lock:
                    in_flight_keys.discard(inference_key)
                slots.release()

            try:
                concurrent_future = self._local_executor.submit(
                    self.detector.infer_sync, device_id, jpeg
                )
            except Exception as exc:
                release_admission()
                self._note_failure(device_id, "local_executor_submit_failed")
                raise TransientInferenceError("local inference submission failed") from exc

            # asyncio cancellation/timeout cannot stop a running thread. Release admission from
            # the concurrent future itself, not its asyncio wrapper (the wrapper becomes cancelled
            # immediately on timeout even while the thread is still running).
            concurrent_future.add_done_callback(lambda _done: release_admission())
            wrapped = asyncio.wrap_future(concurrent_future, loop=loop)

            def consume_late_exception(done: asyncio.Future) -> None:
                # If the phone's deadline wins, the wrapper completes later without an awaiter.
                # Retrieve any exception so asyncio does not report a misleading unhandled future.
                try:
                    done.exception()
                except asyncio.CancelledError:
                    pass

            wrapped.add_done_callback(consume_late_exception)
            outcome = await self._await_inference(device_id, asyncio.shield(wrapped))
        return outcome.detections, outcome.cloud_fallback_unavailable

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
        if (
            header.pose_age_ms is not None
            and header.pose_age_ms <= 100
            and header.pitch_cdeg is not None
            and header.roll_cdeg is not None
        ):
            state.last_pitch_cdeg = header.pitch_cdeg
            state.last_roll_cdeg = header.roll_cdeg
