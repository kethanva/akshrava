import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict

from .composer import hazard_payload, look_summary
from .alert_policy import AlertPolicy
from .detector import Detector
from .domain import FrameHeader, SessionState
from .hazards import HazardScorer
from .tracker import SimpleTracker


class VisionService:
    """Phone-facing vision path.

    Track ID allocation is per-session (`device_id` key). Association state lives on
    `SessionState.tracks`; the helper only owns the next-ID counter so two concurrent
    phones never collide track IDs. This is per-session isolation, not a durable
    cross-reconnect device tracker.
    """

    _POSE_DISCONTINUITY_CDEG = 1_200

    def __init__(
        self,
        detector: Detector,
        store,
        alert_max_age_ms: int = 500,
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
        self._executor = self._new_executor()

    def _new_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self._inference_executor_workers,
            thread_name_prefix="akshrava-inference",
        )

    def _tracker(self, device_id: str) -> SimpleTracker:
        if device_id not in self._trackers:
            self._trackers[device_id] = self._tracker_factory()
        return self._trackers[device_id]

    async def analyze(self, state: SessionState, header: FrameHeader, jpeg: bytes) -> Dict:
        started = time.monotonic()
        detected_started = started
        # Local models and cloud-fallback wrappers retain mutable state. Remote workers opt in
        # to parallel operation explicitly; serializing every other detector prevents cross-phone
        # result attribution and model-runtime races. Noop/remote override requires_serial_execution.
        if self.detector.requires_serial_execution():
            async with self._inference_lock:
                detections, cloud_fallback_unavailable = await self._detect(jpeg)
        else:
            detections, cloud_fallback_unavailable = await self._detect(jpeg)
        detect_ms = int((time.monotonic() - detected_started) * 1000)
        track_score_started = time.monotonic()
        pose_discontinuity = self._pose_discontinuity(state, header)
        tracker = self._tracker(state.device_id)
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
            "priority": is_priority,
            "look_summary": None,
            "late_suppressed": late_suppressed,
            "pipeline_stage_ms": {"detect": detect_ms, "track_score": track_score_ms},
        }
        if cloud_fallback_unavailable is not None:
            result["cloud_fallback_unavailable"] = cloud_fallback_unavailable
        if hazard is not None:
            persist_started = time.monotonic()
            await self.store.record_alert(state.device_id, header.frame_id, hazard)
            result["pipeline_stage_ms"]["persist"] = int((time.monotonic() - persist_started) * 1000)
            result["hazard"] = hazard_payload(hazard, self.language)
        if is_priority:
            # Look answers even when late-suppressed (clear / delayed view) so the explicit
            # query is never silent; stale hazards are still not invented when late.
            result["look_summary"] = look_summary(hazard, self.language)
        return result

    async def release_session(self, device_id: str) -> None:
        """Drop per-connection tracker state on disconnect/revocation."""
        self._trackers.pop(device_id, None)

    def shutdown(self) -> None:
        # cancel_futures is Python 3.9+, while the package intentionally supports Python 3.8.
        # The bounded executor prevents unbounded queued work; wait=False lets shutdown proceed
        # while a non-interruptible native model call finishes on its worker thread.
        self._executor.shutdown(wait=False)
        # TestClient can start a fresh lifespan over the imported application. Recreate the
        # bounded pool lazily instead of retaining an executor that has already been shut down.
        self._executor = self._new_executor()

    async def _detect(self, jpeg: bytes):
        method = getattr(self.detector, "detect_with_status", None)
        loop = asyncio.get_running_loop()
        function = method if method is not None else self.detector.detect
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(self._executor, function, jpeg),
                timeout=self.inference_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("inference deadline exceeded") from exc
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
