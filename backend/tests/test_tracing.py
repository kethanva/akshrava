import re
from unittest.mock import MagicMock, patch

from akshrava_backend import tracing


def test_fallback_traceparent_format():
    tp = tracing._fallback_traceparent()
    # Format: 00-{32 hex}-{16 hex}-01
    pattern = r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$"
    assert re.match(pattern, tp) is not None


def test_ensure_tracer_provider_handles_import_error():
    tracing._PROVIDER_READY = False
    with patch.dict("sys.modules", {"opentelemetry": None}):
        tracing.ensure_tracer_provider()
        assert tracing._PROVIDER_READY is True


def test_ensure_tracer_provider_when_already_ready():
    tracing._PROVIDER_READY = True
    tracing.ensure_tracer_provider()
    assert tracing._PROVIDER_READY is True


def test_inject_trace_headers_fallback():
    headers = {}
    with patch("opentelemetry.trace.propagation.tracecontext.TraceContextTextMapPropagator.inject", side_effect=RuntimeError("OTel error")):
        tracing.inject_trace_headers(headers)
        assert "traceparent" in headers
        assert re.match(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-01$", headers["traceparent"]) is not None


def test_start_inference_span_without_tracer():
    tracing._PROVIDER_READY = False
    with patch.dict("sys.modules", {"opentelemetry": None}):
        with tracing.start_inference_span("test.span"):
            # Inside context block
            pass


def test_start_inference_span_with_tracer():
    mock_tracer = MagicMock()
    mock_span = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

    with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
        with tracing.start_inference_span("test.span"):
            pass
        mock_tracer.start_as_current_span.assert_called_once_with("test.span")
