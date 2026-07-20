"""Small, dependency-free Prometheus metrics for operational safety checks.

Metrics deliberately use no device, frame, or object identifiers as labels: the monitoring
endpoint must not become a source of location or behavioural telemetry.
"""

from threading import Lock
from typing import Dict


class Metrics:
    _INFERENCE_BUCKETS = (50, 100, 180, 250, 350, 500)
    _FRAME_AGE_BUCKETS = (100, 250, 500, 750, 1000, 2000)
    _PIPELINE_STAGES = ("decode", "detect", "track_score", "persist")

    def __init__(self):
        self._lock = Lock()
        self._frames_total = 0
        self._alerts_total = 0
        self._rejected_frames_total = 0
        self._late_suppressed_total = 0
        self._sessions_active = 0
        self._session_admission_rejected_total = 0
        self._inference_failures_total = 0
        self._worker_saturated_total = 0
        self._inference_counts: Dict[int, int] = {bucket: 0 for bucket in self._INFERENCE_BUCKETS}
        self._inference_sum_ms = 0
        self._inference_count = 0
        self._frame_age_counts: Dict[int, int] = {bucket: 0 for bucket in self._FRAME_AGE_BUCKETS}
        self._frame_age_sum_ms = 0
        self._frame_age_count = 0
        self._stage_counts = {stage: {bucket: 0 for bucket in self._INFERENCE_BUCKETS} for stage in self._PIPELINE_STAGES}
        self._stage_sums = {stage: 0 for stage in self._PIPELINE_STAGES}
        self._stage_totals = {stage: 0 for stage in self._PIPELINE_STAGES}

    def observe_result(self, inference_ms: int, has_alert: bool, stage_ms=None) -> None:
        with self._lock:
            self._frames_total += 1
            self._alerts_total += int(has_alert)
            self._inference_sum_ms += inference_ms
            self._inference_count += 1
            for bucket in self._INFERENCE_BUCKETS:
                if inference_ms <= bucket:
                    self._inference_counts[bucket] += 1
            for stage, elapsed in (stage_ms or {}).items():
                if stage not in self._stage_counts or elapsed is None:
                    continue
                elapsed = max(0, int(elapsed))
                self._stage_sums[stage] += elapsed
                self._stage_totals[stage] += 1
                for bucket in self._INFERENCE_BUCKETS:
                    if elapsed <= bucket:
                        self._stage_counts[stage][bucket] += 1

    def observe_frame_age(self, age_ms: int) -> None:
        """Observe capture-to-server age when the phone supplies capture_epoch_ms.

        This is aggregate only: no device, route, or carrier labels. The phone remains the
        authority for glass-to-ear freshness, but this tells operators when uplink/server ingress
        age is already too high before speech arbitration sees the result.
        """
        age_ms = max(0, int(age_ms))
        with self._lock:
            self._frame_age_sum_ms += age_ms
            self._frame_age_count += 1
            for bucket in self._FRAME_AGE_BUCKETS:
                if age_ms <= bucket:
                    self._frame_age_counts[bucket] += 1

    def reject_frame(self) -> None:
        with self._lock:
            self._rejected_frames_total += 1

    def late_suppressed(self) -> None:
        """A hazard existed but arrived past the freshness budget and was never scored/spoken.

        Tracked separately from rejected_frames: this is not a protocol violation, it is the
        server falling behind under load. A rising rate here is the first sign an operator
        should see before users start hearing silence instead of alerts (§9.4).
        """
        with self._lock:
            self._late_suppressed_total += 1

    def session_opened(self) -> None:
        with self._lock:
            self._sessions_active += 1

    def session_closed(self) -> None:
        with self._lock:
            self._sessions_active = max(0, self._sessions_active - 1)

    def session_admission_rejected(self) -> None:
        with self._lock:
            self._session_admission_rejected_total += 1

    def inference_failed(self) -> None:
        with self._lock:
            self._inference_failures_total += 1

    def worker_saturated(self) -> None:
        """Worker returned 503 / queue-full; frame was soft-shed without closing the session."""
        with self._lock:
            self._worker_saturated_total += 1

    def render(self) -> str:
        with self._lock:
            lines = [
                "# HELP akshrava_frames_processed_total Frames successfully processed by the vision service.",
                "# TYPE akshrava_frames_processed_total counter",
                "akshrava_frames_processed_total %s" % self._frames_total,
                "# HELP akshrava_alerts_emitted_total Hazard alerts emitted to a device.",
                "# TYPE akshrava_alerts_emitted_total counter",
                "akshrava_alerts_emitted_total %s" % self._alerts_total,
                "# HELP akshrava_frames_rejected_total Frame messages rejected before inference.",
                "# TYPE akshrava_frames_rejected_total counter",
                "akshrava_frames_rejected_total %s" % self._rejected_frames_total,
                "# HELP akshrava_late_suppressed_total Hazards detected too late to speak safely.",
                "# TYPE akshrava_late_suppressed_total counter",
                "akshrava_late_suppressed_total %s" % self._late_suppressed_total,
                "# HELP akshrava_sessions_active Active authenticated WebSocket sessions on this API instance.",
                "# TYPE akshrava_sessions_active gauge",
                "akshrava_sessions_active %s" % self._sessions_active,
                "# HELP akshrava_session_admission_rejected_total Authenticated sessions rejected because fleet capacity was exhausted.",
                "# TYPE akshrava_session_admission_rejected_total counter",
                "akshrava_session_admission_rejected_total %s" % self._session_admission_rejected_total,
                "# HELP akshrava_inference_failures_total Inference failures that fail closed.",
                "# TYPE akshrava_inference_failures_total counter",
                "akshrava_inference_failures_total %s" % self._inference_failures_total,
                "# HELP akshrava_worker_saturated_total Frames soft-shed because the worker queue was full.",
                "# TYPE akshrava_worker_saturated_total counter",
                "akshrava_worker_saturated_total %s" % self._worker_saturated_total,
                "# HELP akshrava_inference_duration_milliseconds Vision inference and queue duration.",
                "# TYPE akshrava_inference_duration_milliseconds histogram",
            ]
            for bucket in self._INFERENCE_BUCKETS:
                lines.append(
                    'akshrava_inference_duration_milliseconds_bucket{le="%s"} %s'
                    % (bucket, self._inference_counts[bucket])
                )
            lines.extend(
                [
                    'akshrava_inference_duration_milliseconds_bucket{le="+Inf"} %s'
                    % self._inference_count,
                    "akshrava_inference_duration_milliseconds_sum %s" % self._inference_sum_ms,
                    "akshrava_inference_duration_milliseconds_count %s" % self._inference_count,
                ]
            )
            lines.extend(
                [
                    "# HELP akshrava_frame_age_milliseconds Capture epoch to API result age when supplied by the phone; aggregate only.",
                    "# TYPE akshrava_frame_age_milliseconds histogram",
                ]
            )
            for bucket in self._FRAME_AGE_BUCKETS:
                lines.append(
                    'akshrava_frame_age_milliseconds_bucket{le="%s"} %s'
                    % (bucket, self._frame_age_counts[bucket])
                )
            lines.extend(
                [
                    'akshrava_frame_age_milliseconds_bucket{le="+Inf"} %s'
                    % self._frame_age_count,
                    "akshrava_frame_age_milliseconds_sum %s" % self._frame_age_sum_ms,
                    "akshrava_frame_age_milliseconds_count %s" % self._frame_age_count,
                ]
            )
            lines.extend(
                [
                    "# HELP akshrava_pipeline_stage_duration_milliseconds Internal pipeline stage duration; no device or frame labels.",
                    "# TYPE akshrava_pipeline_stage_duration_milliseconds histogram",
                ]
            )
            for stage in self._PIPELINE_STAGES:
                for bucket in self._INFERENCE_BUCKETS:
                    lines.append(
                        'akshrava_pipeline_stage_duration_milliseconds_bucket{stage="%s",le="%s"} %s'
                        % (stage, bucket, self._stage_counts[stage][bucket])
                    )
                lines.append(
                    'akshrava_pipeline_stage_duration_milliseconds_bucket{stage="%s",le="+Inf"} %s'
                    % (stage, self._stage_totals[stage])
                )
                lines.append(
                    'akshrava_pipeline_stage_duration_milliseconds_sum{stage="%s"} %s'
                    % (stage, self._stage_sums[stage])
                )
                lines.append(
                    'akshrava_pipeline_stage_duration_milliseconds_count{stage="%s"} %s'
                    % (stage, self._stage_totals[stage])
                )
            return "\n".join(lines) + "\n"
