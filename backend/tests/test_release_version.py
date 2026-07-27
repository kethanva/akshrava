import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_release_version_script_accepts_the_current_v0212_tag():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_release_version.py"), "v0.2.12"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_release_version_script_rejects_a_mismatched_tag():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_release_version.py"), "v9.9.9"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def _release_checker():
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_release_version

    return check_release_version


def test_served_api_version_matches_the_packaged_release_version():
    """/openapi.json is where an operator reads which build is deployed.

    The version is restated in three places (pyproject, and the FastAPI constructor in each of
    the two services). A stale one makes a rollback look like it did not take effect.
    """
    checker = _release_checker()
    packaged = checker.version_from_backend(ROOT)
    for name, version in checker.served_versions(ROOT).items():
        assert version == packaged, f"{name} serves {version} but the package is {packaged}"


def test_release_manifest_schema_revision_is_derived_from_the_migrations():
    """The revision the API demands must be the one the migrations actually head at.

    Store.verify_schema refuses to start when alembic_version does not match
    DATABASE_SCHEMA_REVISION, so a drift here is a deployment that cannot boot — and the release
    manifest would have told the operator the schema was current.
    """
    checker = _release_checker()
    assert checker.alembic_head_revision(ROOT) == checker.expected_schema_revision(ROOT)


def test_a_forked_migration_history_is_rejected_rather_than_guessed(tmp_path):
    """Two heads means someone branched the migration history; picking one silently is wrong."""
    checker = _release_checker()
    versions = tmp_path / "backend/migrations/versions"
    versions.mkdir(parents=True)
    (versions / "a.py").write_text('revision = "a"\ndown_revision = None\n', encoding="utf-8")
    (versions / "b.py").write_text('revision = "b"\ndown_revision = None\n', encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one migration head"):
        checker.alembic_head_revision(tmp_path)
