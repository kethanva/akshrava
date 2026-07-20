import json
import logging

from akshrava_backend.logging_util import JsonFormatter


def _format(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def test_json_log_line_has_both_a_timestamp_and_a_severity():
    # Regression: both keys were "severity", so the timestamp value was clobbered and every
    # structured log line silently lost its own time field.
    record = logging.LogRecord("akshrava", logging.INFO, __file__, 1, "hello", None, None)
    out = _format(record)
    assert out["severity"] == "INFO"
    assert out["time"].endswith("Z")
    assert out["message"] == "hello"
    assert out["logger"] == "akshrava"


def test_json_log_includes_optional_correlation_fields_when_present():
    record = logging.LogRecord("akshrava", logging.WARNING, __file__, 1, "late", None, None)
    record.trace_id = "abc123"
    record.status = 504
    out = _format(record)
    assert out["trace_id"] == "abc123"
    assert out["status"] == 504
    assert out["severity"] == "WARNING"
