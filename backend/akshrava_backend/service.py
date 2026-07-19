import asyncio
import time
from typing import Dict

from .detector import Detector
from .domain import FrameHeader, SessionState
from .hazards import HazardScorer
from .tracker import SimpleTracker


class VisionService:
    _POSE_DISCONTINUITY_CDEG = 1_200
    def __init__(self, detector: Detector, store, alert_max_age_ms: int = 500):
        self.detector = detector
        self.store = store
        self.tracker = SimpleTracker()
        self.scorer = HazardScorer()
        self._inference_lock = asyncio.Lock()
        self.alert_max_age_ms = alert_max_age_ms

    async def analyze(self, state: SessionState, header: FrameHeader, jpeg: bytes) -> Dict:
        started = time.monotonic()
        detected_started = started
        # Local models and cloud-fallback wrappers retain mutable state. Remote workers opt in
        # to parallel operation explicitly; serializing every other detector prevents cross-phone
        # result attribution and model-runtime races.
        if self.detector.requires_serial_execution():
            async with self._inference_lock:
                detections, cloud_fallback_unavailable = await self._detect(jpeg)
        else:
            detections, cloud_fallback_unavailable = await self._detect(jpeg)
        detect_ms = int((time.monotonic() - detected_started) * 1000)
        track_score_started = time.monotonic()
        pose_discontinuity = self._pose_discontinuity(state, header)
        state.tracks = self.tracker.update(
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
        hazard = None
        if not late_suppressed:
            hazard = self.scorer.score(
                state,
                header.width,
                header.height,
                header.pose_age_ms,
                header.pitch_cdeg,
                header.roll_cdeg,
                state.geometry_profile,
            )
        track_score_ms = int((time.monotonic() - track_score_started) * 1000)

        result = {
            "type": "result",
            "frame_id": header.frame_id,
            "capture_mono_ms": header.capture_mono_ms,
            "server_inference_ms": inference_ms,
            "server_received_epoch_ms": int(time.time() * 1000),
            "hazard": None,
            "late_suppressed": late_suppressed,
            "pipeline_stage_ms": {"detect": detect_ms, "track_score": track_score_ms},
        }
        if cloud_fallback_unavailable is not None:
            result["cloud_fallback_unavailable"] = cloud_fallback_unavailable
        if hazard is not None:
            persist_started = time.monotonic()
            await self.store.record_alert(state.device_id, header.frame_id, hazard)
            result["pipeline_stage_ms"]["persist"] = int((time.monotonic() - persist_started) * 1000)
            result["hazard"] = {
                "kind": hazard.kind,
                "level": hazard.level,
                "severity": hazard.severity,
                "bearing": hazard.bearing,
                "message_key": hazard.message_key,
                "haptic": hazard.haptic,
                "confidence": round(hazard.confidence, 3),
                "range_band": hazard.range_band,
                "range_valid": hazard.range_valid,
                "motion_evidence": "insufficient",
            }
        return result

    async def _detect(self, jpeg: bytes):
        method = getattr(self.detector, "detect_with_status", None)
        if method is not None:
            return await asyncio.get_running_loop().run_in_executor(None, method, jpeg)
        detections = await asyncio.get_running_loop().run_in_executor(None, self.detector.detect, jpeg)
        return detections, None

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
