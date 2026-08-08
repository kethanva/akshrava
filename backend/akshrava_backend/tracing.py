"""W3C Trace Context helpers for API → worker correlation.

OpenTelemetry is optional at import time so unit tests without the package still run;
production images should install `opentelemetry-api` (see pyproject.toml).
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_PROVIDER_READY = False


def _fallback_traceparent() -> str:
    """A valid W3C traceparent generated without OpenTelemetry.

    The full OTel SDK is optional (not a base dependency). Without it the worker's `_trace_id`
    log correlation would receive nothing, so emit a spec-compliant
    ``00-<16-byte trace-id>-<8-byte span-id>-01`` here. When OTel *is* installed its propagator
    takes precedence (this is only used as the except-path fallback), so a real distributed trace
    still stitches correctly; otherwise the phone→API→worker legs at least share one id in logs.
    """
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def _attach_span_exporter(provider) -> None:
    """Attach an OTLP exporter when one is configured, and say so when one is not.

    Without a span processor a TracerProvider builds spans and drops them on the floor: the
    tracing code all runs, costs CPU on a donated phone's request path, and produces nothing an
    operator can ever look at. That silent no-op is worse than having no tracing at all, because
    it reads as coverage. Either export, or state plainly why not.

    Configuration is the OpenTelemetry standard `OTEL_EXPORTER_OTLP_ENDPOINT`, and the exporter
    package is an optional dependency (`pip install '.[otel]'`) so the base image stays small.
    Nothing here may raise: a telemetry backend being unreachable must never take down an
    inference path a blind user is walking with.
    """
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.info(
            "tracing: no OTEL_EXPORTER_OTLP_ENDPOINT configured; spans are created for "
            "traceparent propagation only and are not exported"
        )
        return
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:
        logger.warning(
            "tracing: OTEL_EXPORTER_OTLP_ENDPOINT is set but the OTLP exporter is not installed; "
            "spans will NOT be exported (install the 'otel' extra)"
        )
        return
    try:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        logger.info("tracing: exporting spans via OTLP to %s", endpoint)
    except Exception:
        logger.warning("tracing: failed to attach the OTLP span exporter", exc_info=True)


def ensure_tracer_provider() -> None:
    global _PROVIDER_READY
    if _PROVIDER_READY:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
    except Exception:
        _PROVIDER_READY = True
        return
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": os.getenv("OTEL_SERVICE_NAME", "akshrava-api"),
                    "deployment.environment": os.getenv("AKSHRAVA_ENV", "development"),
                }
            )
        )
        _attach_span_exporter(provider)
        trace.set_tracer_provider(provider)
    _PROVIDER_READY = True


def inject_trace_headers(headers: MutableMapping[str, str]) -> None:
    """Inject a W3C `traceparent` into an outbound header map (mutates in place).

    Always injects: with OpenTelemetry present the propagator sets a real distributed-trace
    parent; without it (or if inject leaves no header) a spec-compliant fallback id is written so
    the worker still has one correlation id to log. Never raises -- tracing must not break inference.
    """
    ensure_tracer_provider()
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        TraceContextTextMapPropagator().inject(headers)
    except Exception:
        pass
    if not headers.get("traceparent"):
        headers["traceparent"] = _fallback_traceparent()


@contextmanager
def start_inference_span(name: str = "akshrava.inference") -> Iterator[None]:
    """Create a short-lived span so inject() has a valid parent context."""
    ensure_tracer_provider()
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("akshrava")
    except Exception:
        tracer = None
        
    if tracer is None:
        yield
        return
        
    # Keep the span `with` outside exception handlers so WorkerSaturatedError (and any other
    # inference failure) propagates cleanly — a broad except around yield re-enters the
    # generator and raises "generator didn't stop after throw()".
    with tracer.start_as_current_span(name):
        yield
