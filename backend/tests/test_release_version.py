import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_release_version_script_accepts_the_tag_for_the_packaged_version():
    # Derived, not hardcoded: a literal here has to be edited on every bump, and the one that
    # used to live here had already drifted out of step with its own test name. What this
    # asserts is unchanged -- that all four version sites and the schema revision agree with
    # the packaged version -- and the mismatch case is covered by the test below.
    version = _release_checker().version_from_backend(ROOT)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_release_version.py"), f"v{version}"],
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


def test_every_ios_version_site_matches_the_packaged_release_version():
    """The iOS version is declared in three files and all three reach a shipped build.

    Info.plist is what an installed IPA reports, project.yml MARKETING_VERSION is what the
    xcodegen-produced archive is stamped with, and AppConfig.swift is what the app itself logs.
    Checking only the Swift constant would let a release publish an IPA carrying the previous
    version while the gate reported parity.
    """
    checker = _release_checker()
    packaged = checker.version_from_backend(ROOT)
    sites = checker.ios_versions(ROOT)
    assert set(sites) == {"ios-appconfig", "ios-infoplist", "ios-xcodegen"}
    for name, version in sites.items():
        assert version == packaged, f"{name} declares {version} but the package is {packaged}"


def test_a_missing_ios_release_file_fails_the_gate_instead_of_falling_back(tmp_path):
    """A deleted or moved iOS tree must block a release, not silently report parity.

    The first version of this check returned the backend version when AppConfig.swift was absent,
    so the gate passed precisely when it could not verify anything.
    """
    checker = _release_checker()
    with pytest.raises(ValueError, match="iOS release file is missing"):
        checker.ios_versions(tmp_path)


def test_a_forked_migration_history_is_rejected_rather_than_guessed(tmp_path):
    """Two heads means someone branched the migration history; picking one silently is wrong."""
    checker = _release_checker()
    versions = tmp_path / "backend/migrations/versions"
    versions.mkdir(parents=True)
    (versions / "a.py").write_text('revision = "a"\ndown_revision = None\n', encoding="utf-8")
    (versions / "b.py").write_text('revision = "b"\ndown_revision = None\n', encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one migration head"):
        checker.alembic_head_revision(tmp_path)
