import pytest

from akshrava_backend.storage import _alert_event_add_column_sql


def test_alert_event_migration_sql_uses_only_reviewed_fragments():
    assert _alert_event_add_column_sql("track_id") == "ALTER TABLE alert_events ADD COLUMN track_id INTEGER"


def test_alert_event_migration_sql_rejects_unknown_columns():
    with pytest.raises(ValueError):
        _alert_event_add_column_sql("track_id; DROP TABLE alert_events")
