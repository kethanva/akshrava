from akshrava_backend.metrics import Metrics


def test_metrics_render_session_admission_and_frame_age_slo_series():
    metrics = Metrics()
    metrics.session_admission_rejected()
    metrics.observe_frame_age(240)

    rendered = metrics.render()

    assert "akshrava_session_admission_rejected_total 1" in rendered
    assert "akshrava_frame_age_milliseconds_sum 240" in rendered
    assert "akshrava_frame_age_milliseconds_count 1" in rendered
    assert 'akshrava_frame_age_milliseconds_bucket{le="250"} 1' in rendered


def test_phone_acknowledgement_metrics_distinguish_transport_from_handset_freshness():
    """A server write is not delivery; only the authenticated phone can report its own gate."""
    metrics = Metrics()
    for _ in range(3):
        metrics.result_sent(acknowledgement_expected=True)
    metrics.phone_result_acknowledged(fresh=True)
    metrics.phone_result_acknowledged(fresh=True)
    metrics.phone_result_acknowledged(fresh=False)

    rendered = metrics.render()
    assert "akshrava_results_sent_total 3" in rendered
    assert "akshrava_phone_results_acknowledged_total 3" in rendered
    assert "akshrava_phone_results_acknowledged_fresh_total 2" in rendered
    assert "# TYPE akshrava_results_sent_total counter" in rendered
    assert "# TYPE akshrava_phone_results_acknowledged_fresh_total counter" in rendered
    assert metrics.take_phone_delivery_window() == (3, 3, 3, 2, 0)
    assert metrics.take_phone_delivery_window() == (0, 0, 0, 0, 0)


def test_worker_processing_does_not_fabricate_phone_delivery_metrics():
    """The GPU worker has no phone WebSocket and therefore cannot report delivery."""
    metrics = Metrics()
    metrics.observe_result(50, False)
    rendered = metrics.render()
    assert "akshrava_results_sent_total 0" in rendered
    assert "akshrava_phone_results_acknowledged_total 0" in rendered
    assert "akshrava_phone_results_acknowledged_fresh_total 0" in rendered
    # ...while the ordinary frame counter still moves.
    assert "akshrava_frames_processed_total 1" in rendered


def test_legacy_phone_results_are_not_expected_to_acknowledge():
    metrics = Metrics()
    metrics.result_sent(acknowledgement_expected=False)
    assert metrics.take_phone_delivery_window() == (1, 0, 0, 0, 0)


def test_unacknowledged_results_are_counted_exactly_not_derived():
    """The alertable delivery-failure signal must not be `expected - acknowledged`.

    Those two totals are observed in different export windows for any frame still in flight, so
    the subtraction reported phantom failures proportional to fleet size. Only an explicit
    eviction -- the phone demonstrably never acknowledged a result it was sent -- counts.
    """
    metrics = Metrics()
    # Two results sent, one acknowledged, and the other proven missing by eviction.
    metrics.result_sent(acknowledgement_expected=True)
    metrics.result_sent(acknowledgement_expected=True)
    metrics.phone_result_acknowledged(fresh=True)
    metrics.phone_result_unacknowledged()

    assert "akshrava_phone_results_unacknowledged_total 1" in metrics.render()
    assert metrics.take_phone_delivery_window() == (2, 2, 1, 1, 1)


def test_a_result_still_in_flight_is_never_reported_as_missing():
    """Regression: sent-but-not-yet-acknowledged is not a failure, it is normal steady state."""
    metrics = Metrics()
    for _ in range(5):
        metrics.result_sent(acknowledgement_expected=True)
    sent, expected, acknowledged, fresh, unacknowledged = metrics.take_phone_delivery_window()
    assert (sent, expected, acknowledged, fresh) == (5, 5, 0, 0)
    assert unacknowledged == 0


def test_phone_acknowledgement_can_report_only_late_results():
    """The phone's freshness gate, not server inference time, determines this counter."""
    metrics = Metrics()
    for _ in range(4):
        metrics.result_sent(acknowledgement_expected=True)
        metrics.phone_result_acknowledged(fresh=False)
    rendered = metrics.render()
    assert "akshrava_results_sent_total 4" in rendered
    assert "akshrava_phone_results_acknowledged_total 4" in rendered
    assert "akshrava_phone_results_acknowledged_fresh_total 0" in rendered
