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


def test_tracing_states_plainly_when_spans_are_not_exported(monkeypatch, caplog):
    """A TracerProvider with no span processor silently drops every span.

    That is worse than no tracing: the code runs, costs request-path CPU, and reads as coverage
    while producing nothing an operator can look at. Require it to say so.
    """
    import logging

    import akshrava_backend.tracing as tracing_mod

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    with caplog.at_level(logging.INFO, logger="akshrava_backend.tracing"):
        tracing_mod._attach_span_exporter(object())
    assert any("not exported" in record.message for record in caplog.records)


def test_tracing_warns_when_an_endpoint_is_configured_but_the_exporter_is_missing(
    monkeypatch, caplog
):
    """Configured-but-not-installed must be loud: the operator believes they have traces."""
    import builtins
    import logging

    import akshrava_backend.tracing as tracing_mod

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if "exporter.otlp" in name:
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with caplog.at_level(logging.WARNING, logger="akshrava_backend.tracing"):
        tracing_mod._attach_span_exporter(object())
    assert any("will NOT be exported" in record.message for record in caplog.records)


def test_attaching_an_exporter_never_raises_into_the_inference_path(monkeypatch):
    """A telemetry backend problem must never break a session a user is walking with.

    The OTLP exporter is an optional extra and is not installed in the test environment, so this
    test must inject a stub module -- otherwise _attach_span_exporter returns at the ImportError
    guard and the failure path below is never executed at all (a vacuously passing test).
    """
    import sys
    import types

    import akshrava_backend.tracing as tracing_mod

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")

    exporter_module = types.ModuleType(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    )
    exporter_module.OTLPSpanExporter = lambda *args, **kwargs: object()
    export_module = types.ModuleType("opentelemetry.sdk.trace.export")
    export_module.BatchSpanProcessor = lambda *args, **kwargs: object()
    monkeypatch.setitem(
        sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", exporter_module
    )
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace.export", export_module)

    attached = {"called": False}

    class Exploding:
        def add_span_processor(self, _processor):
            attached["called"] = True
            raise RuntimeError("collector unreachable")

    # Must not propagate: telemetry is never a reason to fail inference.
    tracing_mod._attach_span_exporter(Exploding())
    assert attached["called"], "the exporter attach path was never reached; test would be vacuous"


def test_a_healthy_exporter_is_actually_attached(monkeypatch):
    """Positive case: with an endpoint and the package present, a processor is installed."""
    import sys
    import types

    import akshrava_backend.tracing as tracing_mod

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")

    exporter_module = types.ModuleType(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    )
    exporter_module.OTLPSpanExporter = lambda *args, **kwargs: "exporter"
    export_module = types.ModuleType("opentelemetry.sdk.trace.export")
    export_module.BatchSpanProcessor = lambda exporter: ("processor", exporter)
    monkeypatch.setitem(
        sys.modules, "opentelemetry.exporter.otlp.proto.http.trace_exporter", exporter_module
    )
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace.export", export_module)

    installed = []

    class Provider:
        def add_span_processor(self, processor):
            installed.append(processor)

    tracing_mod._attach_span_exporter(Provider())
    assert installed == [("processor", "exporter")]
