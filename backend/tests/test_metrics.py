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
