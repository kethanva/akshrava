from pathlib import Path


def test_production_schema_is_owned_by_a_versioned_alembic_revision():
    root = Path(__file__).resolve().parents[1]
    revision = root / "migrations" / "versions" / "20260719_01_initial_schema.py"
    assert revision.is_file()
    source = revision.read_text()
    assert 'revision = "20260719_01"' in source
    assert "op.create_table(" in source


def test_runtime_storage_contains_no_schema_alter_statements():
    source = (Path(__file__).resolve().parents[1] / "akshrava_backend" / "storage.py").read_text()
    assert "ALTER TABLE" not in source
    assert "create_all" in source  # development/test bootstrap only
