"""Small, dependency-free Prometheus metrics for operational safety checks.

Metrics deliberately use no device, frame, or object identifiers as labels: the monitoring
endpoint must not become a source of location or behavioural telemetry.
"""

from threading import Lock


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
        self._late_capture_suppressed_total = 0
        self._alerts_rate_limited_total = 0
        self._alerts_debounced_total = 0
        self._control_messages_rejected_total = 0
        self._session_superseded_total = 0
        self._sessions_active = 0
        self._session_admission_rejected_total = 0
        self._inference_failures_total = 0
        self._worker_saturated_total = 0
        self._inference_circuit_open_total = 0
        # ---- phone result delivery ----
        # A successful server WebSocket write is not delivery: the peer can disappear before it
        # processes the frame. Keep that transport fact separate from the phone's explicit ack,
        # which is the only signal that proves the result reached the handset and passed its own
        # glass-to-ear freshness gate. Neither metric claims that an utterance was audible; TTS
        # acceptance is intentionally not inferred from a network protocol acknowledgement.
        self._results_sent_total = 0
        self._result_acknowledgements_expected_total = 0
        self._phone_results_acknowledged_total = 0
        self._phone_results_acknowledged_fresh_total = 0
        # Counted exactly, at the moment a pending acknowledgement slot is evicted without ever
        # having been acknowledged -- NOT derived as (expected - acknowledged) over an export
        # window. That subtraction was structurally wrong: the two totals are observed in
        # different 60s windows for any frame in flight across a window boundary, so at a
        # realistic fleet size the derived value was permanently nonzero even when every result
        # was acknowledged promptly. An eviction is unambiguous evidence the phone never acked.
        self._phone_results_unacknowledged_total = 0
        self._delivery_reported_sent_total = 0
        self._delivery_reported_expected_total = 0
        self._delivery_reported_acknowledged_total = 0
        self._delivery_reported_fresh_total = 0
        self._delivery_reported_unacknowledged_total = 0
        self._db_pool_checkedin = 0
        self._db_pool_checkedout = 0
        self._inference_counts: dict[int, int] = {bucket: 0 for bucket in self._INFERENCE_BUCKETS}
        self._inference_sum_ms = 0
        self._inference_count = 0
        self._frame_age_counts: dict[int, int] = {bucket: 0 for bucket in self._FRAME_AGE_BUCKETS}
        self._frame_age_sum_ms = 0
        self._frame_age_count = 0
        self._stage_counts = {stage: {bucket: 0 for bucket in self._INFERENCE_BUCKETS} for stage in self._PIPELINE_STAGES}
        self._stage_sums = {stage: 0 for stage in self._PIPELINE_STAGES}
        self._stage_totals = {stage: 0 for stage in self._PIPELINE_STAGES}

    def observe_result(
        self,
        inference_ms: int,
        has_alert: bool,
        stage_ms=None,
    ) -> None:
        """Record one inference result produced by this process.

        Transport delivery is recorded separately after a successful WebSocket write, and phone
        receipt/freshness is recorded only after the bounded result acknowledgement arrives.
        """
        with self._lock:
            self._frames_total += 1
            self._alerts_total += int(has_alert)
            inference_ms = max(0, int(inference_ms))
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

    def late_capture_suppressed(self) -> None:
        """Late because capture-to-receive age (not detector wall time) exceeded the budget."""
        with self._lock:
            self._late_capture_suppressed_total += 1

    def alert_rate_limited(self) -> None:
        with self._lock:
            self._alerts_rate_limited_total += 1

    def alert_debounced(self) -> None:
        with self._lock:
            self._alerts_debounced_total += 1

    def control_message_rejected(self) -> None:
        with self._lock:
            self._control_messages_rejected_total += 1

    def session_superseded(self) -> None:
        with self._lock:
            self._session_superseded_total += 1

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

    def inference_circuit_open(self) -> None:
        """A per-device breaker rejected a frame after sustained inference failures."""
        with self._lock:
            self._inference_circuit_open_total += 1

    def result_sent(self, *, acknowledgement_expected: bool) -> None:
        """A result was accepted by the server-side WebSocket transport."""
        with self._lock:
            self._results_sent_total += 1
            self._result_acknowledgements_expected_total += int(acknowledgement_expected)

    def phone_result_acknowledged(self, *, fresh: bool) -> None:
        """The authenticated phone processed a sent result and reported its own freshness gate."""
        with self._lock:
            self._phone_results_acknowledged_total += 1
            self._phone_results_acknowledged_fresh_total += int(fresh)

    def phone_result_unacknowledged(self) -> None:
        """A sent result aged out of the bounded pending-ack set without ever being acknowledged.

        This is the alertable delivery-failure signal, and it is exact: the session sent a result,
        kept a slot for its acknowledgement, and later discarded that slot because newer results
        pushed it out. No window arithmetic is involved, so it cannot report a false failure for a
        frame that is merely still in flight.
        """
        with self._lock:
            self._phone_results_unacknowledged_total += 1

    def take_phone_delivery_window(self) -> tuple[int, int, int, int, int]:
        """Return one process-local delivery delta for export without device/frame labels.

        Cloud Run does not scrape this private endpoint by itself. The API therefore logs bounded
        aggregate deltas periodically, which Terraform turns into Cloud Logging metrics. Tracking
        the last reported totals under the same lock preserves counter correctness across a
        concurrent result send/ack while never retaining user-specific state.
        """
        with self._lock:
            sent = self._results_sent_total - self._delivery_reported_sent_total
            expected = self._result_acknowledgements_expected_total - self._delivery_reported_expected_total
            acknowledged = self._phone_results_acknowledged_total - self._delivery_reported_acknowledged_total
            fresh = self._phone_results_acknowledged_fresh_total - self._delivery_reported_fresh_total
            unacknowledged = (
                self._phone_results_unacknowledged_total - self._delivery_reported_unacknowledged_total
            )
            self._delivery_reported_sent_total = self._results_sent_total
            self._delivery_reported_expected_total = self._result_acknowledgements_expected_total
            self._delivery_reported_acknowledged_total = self._phone_results_acknowledged_total
            self._delivery_reported_fresh_total = self._phone_results_acknowledged_fresh_total
            self._delivery_reported_unacknowledged_total = self._phone_results_unacknowledged_total
            return sent, expected, acknowledged, fresh, unacknowledged

    def set_db_pool_stats(self, checkedin: int, checkedout: int) -> None:
        with self._lock:
            self._db_pool_checkedin = checkedin
            self._db_pool_checkedout = checkedout


    def render(self) -> str:
        with self._lock:
            lines = [
                "# HELP akshrava_frames_processed_total Frames successfully processed by the vision service.",
                "# TYPE akshrava_frames_processed_total counter",
                f"akshrava_frames_processed_total {self._frames_total}",
                "# HELP akshrava_alerts_emitted_total Hazard alerts emitted to a device.",
                "# TYPE akshrava_alerts_emitted_total counter",
                f"akshrava_alerts_emitted_total {self._alerts_total}",
                "# HELP akshrava_frames_rejected_total Frame messages rejected before inference.",
                "# TYPE akshrava_frames_rejected_total counter",
                f"akshrava_frames_rejected_total {self._rejected_frames_total}",
                "# HELP akshrava_late_suppressed_total Hazards detected outside the speech freshness budget.",
                "# TYPE akshrava_late_suppressed_total counter",
                f"akshrava_late_suppressed_total {self._late_suppressed_total}",
                "# HELP akshrava_late_capture_suppressed_total Frames suppressed because capture-to-receive age exceeded the speech budget.",
                "# TYPE akshrava_late_capture_suppressed_total counter",
                f"akshrava_late_capture_suppressed_total {self._late_capture_suppressed_total}",
                "# HELP akshrava_alerts_rate_limited_total Scored hazards dropped by the 60s global backstop.",
                "# TYPE akshrava_alerts_rate_limited_total counter",
                f"akshrava_alerts_rate_limited_total {self._alerts_rate_limited_total}",
                "# HELP akshrava_alerts_debounced_total Scored hazards dropped by the same-key debounce.",
                "# TYPE akshrava_alerts_debounced_total counter",
                f"akshrava_alerts_debounced_total {self._alerts_debounced_total}",
                "# HELP akshrava_control_messages_rejected_total Malformed control frames soft-shed without closing the socket.",
                "# TYPE akshrava_control_messages_rejected_total counter",
                f"akshrava_control_messages_rejected_total {self._control_messages_rejected_total}",
                "# HELP akshrava_session_superseded_total Live sockets closed because a newer session for the same device took over.",
                "# TYPE akshrava_session_superseded_total counter",
                f"akshrava_session_superseded_total {self._session_superseded_total}",
                "# HELP akshrava_sessions_active Active authenticated WebSocket sessions on this API instance.",
                "# TYPE akshrava_sessions_active gauge",
                f"akshrava_sessions_active {self._sessions_active}",
                "# HELP akshrava_session_admission_rejected_total Authenticated sessions rejected because fleet capacity was exhausted.",
                "# TYPE akshrava_session_admission_rejected_total counter",
                f"akshrava_session_admission_rejected_total {self._session_admission_rejected_total}",
                "# HELP akshrava_inference_failures_total Inference failures that fail closed.",
                "# TYPE akshrava_inference_failures_total counter",
                f"akshrava_inference_failures_total {self._inference_failures_total}",
                "# HELP akshrava_worker_saturated_total Frames soft-shed because the worker queue was full.",
                "# TYPE akshrava_worker_saturated_total counter",
                f"akshrava_worker_saturated_total {self._worker_saturated_total}",
                "# HELP akshrava_inference_circuit_open_total Frames shed while a per-device inference circuit was open.",
                "# TYPE akshrava_inference_circuit_open_total counter",
                f"akshrava_inference_circuit_open_total {self._inference_circuit_open_total}",
                "# HELP akshrava_results_sent_total Results accepted by the server-side WebSocket transport; this is not handset delivery.",
                "# TYPE akshrava_results_sent_total counter",
                f"akshrava_results_sent_total {self._results_sent_total}",
                "# HELP akshrava_result_acknowledgements_expected_total Sent results for phones that explicitly support result acknowledgements.",
                "# TYPE akshrava_result_acknowledgements_expected_total counter",
                f"akshrava_result_acknowledgements_expected_total {self._result_acknowledgements_expected_total}",
                "# HELP akshrava_phone_results_acknowledged_total Results the authenticated phone explicitly processed.",
                "# TYPE akshrava_phone_results_acknowledged_total counter",
                f"akshrava_phone_results_acknowledged_total {self._phone_results_acknowledged_total}",
                "# HELP akshrava_phone_results_acknowledged_fresh_total Phone-acknowledged results inside the phone freshness gate; this does not assert TTS playback.",
                "# TYPE akshrava_phone_results_acknowledged_fresh_total counter",
                f"akshrava_phone_results_acknowledged_fresh_total {self._phone_results_acknowledged_fresh_total}",
                "# HELP akshrava_phone_results_unacknowledged_total Sent results whose bounded acknowledgement slot was evicted without the phone ever acknowledging them.",
                "# TYPE akshrava_phone_results_unacknowledged_total counter",
                f"akshrava_phone_results_unacknowledged_total {self._phone_results_unacknowledged_total}",
                "# HELP akshrava_db_pool_checkedin SQLAlchemy connection pool idle count.",
                "# TYPE akshrava_db_pool_checkedin gauge",
                f"akshrava_db_pool_checkedin {self._db_pool_checkedin}",
                "# HELP akshrava_db_pool_checkedout SQLAlchemy connection pool active count.",
                "# TYPE akshrava_db_pool_checkedout gauge",
                f"akshrava_db_pool_checkedout {self._db_pool_checkedout}",
                "# HELP akshrava_inference_duration_milliseconds Vision inference and queue duration.",
                "# TYPE akshrava_inference_duration_milliseconds histogram",
            ]
            for bucket in self._INFERENCE_BUCKETS:
                lines.append(
                    f'akshrava_inference_duration_milliseconds_bucket{{le="{bucket}"}} {self._inference_counts[bucket]}'
                )
            lines.extend(
                [
                    f'akshrava_inference_duration_milliseconds_bucket{{le="+Inf"}} {self._inference_count}',
                    f"akshrava_inference_duration_milliseconds_sum {self._inference_sum_ms}",
                    f"akshrava_inference_duration_milliseconds_count {self._inference_count}",
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
                    f'akshrava_frame_age_milliseconds_bucket{{le="{bucket}"}} {self._frame_age_counts[bucket]}'
                )
            lines.extend(
                [
                    f'akshrava_frame_age_milliseconds_bucket{{le="+Inf"}} {self._frame_age_count}',
                    f"akshrava_frame_age_milliseconds_sum {self._frame_age_sum_ms}",
                    f"akshrava_frame_age_milliseconds_count {self._frame_age_count}",
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
                        f'akshrava_pipeline_stage_duration_milliseconds_bucket{{stage="{stage}",le="{bucket}"}} {self._stage_counts[stage][bucket]}'
                    )
                lines.append(
                    f'akshrava_pipeline_stage_duration_milliseconds_bucket{{stage="{stage}",le="+Inf"}} {self._stage_totals[stage]}'
                )
                lines.append(
                    f'akshrava_pipeline_stage_duration_milliseconds_sum{{stage="{stage}"}} {self._stage_sums[stage]}'
                )
                lines.append(
                    f'akshrava_pipeline_stage_duration_milliseconds_count{{stage="{stage}"}} {self._stage_totals[stage]}'
                )
            return "\n".join(lines) + "\n"
