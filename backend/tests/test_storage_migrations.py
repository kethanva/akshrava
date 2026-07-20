from pathlib import Path


def test_production_schema_is_owned_by_a_versioned_alembic_revision():
    root = Path(__file__).resolve().parents[1]
    initial = root / "migrations" / "versions" / "20260719_01_initial_schema.py"
    assert initial.is_file()
    assert 'revision = "20260719_01"' in initial.read_text()
    assert "op.create_table(" in initial.read_text()
    head = root / "migrations" / "versions" / "20260721_01_reference_height_px.py"
    assert head.is_file()
    head_source = head.read_text()
    assert 'revision = "20260721_01"' in head_source
    assert 'down_revision = "20260719_01"' in head_source
    assert "reference_height_px" in head_source


def test_runtime_storage_contains_no_schema_alter_statements():
    source = (Path(__file__).resolve().parents[1] / "akshrava_backend" / "storage.py").read_text()
    assert "ALTER TABLE" not in source
    assert "create_all" in source  # development/test bootstrap only
