#!/usr/bin/env python3
"""Ensure a vX.Y.Z tag names the exact backend and Android release version.

Also pins the identifiers that are declared in more than one place and would otherwise drift
apart silently: the version FastAPI serves, and the database schema revision an operator reads
off the release manifest to decide which migration a deployment needs.
"""

import re
import sys
from pathlib import Path
from typing import Dict


def version_from_backend(root: Path) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (root / "backend/pyproject.toml").read_text(), re.M)
    if not match:
        raise ValueError("backend project version is missing")
    return match.group(1)


def version_from_android(root: Path) -> str:
    match = re.search(r'^\s*versionName\s*=\s*"([^"]+)"', (root / "android/app/build.gradle.kts").read_text(), re.M)
    if not match:
        raise ValueError("Android versionName is missing")
    return match.group(1)


def _fastapi_version(path: Path) -> str:
    """The version string a service advertises on /openapi.json."""
    match = re.search(r'FastAPI\((?:[^)]*?)version="([^"]+)"', path.read_text(), re.S)
    if not match:
        raise ValueError("FastAPI application version is missing in %s" % path.name)
    return match.group(1)


def served_versions(root: Path) -> Dict[str, str]:
    return {
        "api-app": _fastapi_version(root / "backend/akshrava_backend/main.py"),
        "worker-app": _fastapi_version(root / "backend/akshrava_backend/worker.py"),
    }


def alembic_head_revision(root: Path) -> str:
    """The single migration no other migration points back to.

    Deriving it beats hardcoding it in the release workflow: a manifest that names the wrong
    revision tells an operator a deployment is migrated when it is not, and the API refuses to
    start against a schema whose alembic_version does not match what it expects.
    """
    revisions = {}
    down_revisions = set()
    for path in sorted((root / "backend/migrations/versions").glob("*.py")):
        text = path.read_text()
        revision = re.search(r'^revision\s*=\s*"([^"]+)"', text, re.M)
        down = re.search(r'^down_revision\s*=\s*(?:"([^"]+)"|None)', text, re.M)
        if revision is None or down is None:
            raise ValueError("migration %s must declare revision and down_revision" % path.name)
        revisions[revision.group(1)] = path.name
        if down.group(1):
            down_revisions.add(down.group(1))
    heads = sorted(set(revisions) - down_revisions)
    if len(heads) != 1:
        raise ValueError("expected exactly one migration head, found %s" % (heads or "none"))
    return heads[0]


def expected_schema_revision(root: Path) -> str:
    """The revision the API refuses to start without outside development (config.Settings)."""
    text = (root / "backend/akshrava_backend/config.py").read_text()
    match = re.search(r'os\.getenv\(\s*"DATABASE_SCHEMA_REVISION"\s*,\s*"([^"]+)"\s*\)', text)
    if not match:
        raise ValueError("DATABASE_SCHEMA_REVISION default is missing from config.py")
    return match.group(1)


def ios_versions(root: Path) -> Dict[str, str]:
    """Every place the iOS release version is declared, checked independently.

    Three files decide what an installed build reports, and only one of them is Swift. The IPA's
    user-visible version comes from Info.plist, the archive's from project.yml MARKETING_VERSION,
    and the in-app / telemetry one from AppConfig.swift. Checking a single file would let a
    release ship an IPA stamped with the previous version while this gate reported success.

    There is deliberately no "iOS directory missing" fallback. An earlier version returned the
    backend version when AppConfig.swift was absent, which made the gate pass by *not finding*
    the thing it exists to verify -- a deleted or renamed iOS tree would have been reported as
    parity rather than as the release-blocking problem it is.
    """
    sources = {
        "ios-appconfig": (
            "ios/Akshrava/Akshrava/AppConfig.swift",
            r'public let appVersion: String = "([^"]+)"',
        ),
        "ios-infoplist": (
            "ios/AkshravaApp/Info.plist",
            r"<key>CFBundleShortVersionString</key>\s*<string>([^<]+)</string>",
        ),
        "ios-xcodegen": (
            "ios/AkshravaApp/project.yml",
            r"^\s*MARKETING_VERSION:\s*\"?([0-9][^\"\s]*)\"?\s*$",
        ),
    }
    found = {}
    for name, (relative, pattern) in sources.items():
        path = root / relative
        if not path.exists():
            raise ValueError("iOS release file is missing: %s" % relative)
        match = re.search(pattern, path.read_text(), re.M)
        if not match:
            raise ValueError("iOS release version is missing from %s" % relative)
        found[name] = match.group(1)
    return found


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", sys.argv[1]):
        print("usage: check_release_version.py vX.Y.Z", file=sys.stderr)
        return 2
    expected = sys.argv[1][1:]
    root = Path(__file__).resolve().parents[1]
    versions = {
        "backend": version_from_backend(root),
        "android": version_from_android(root),
        **ios_versions(root),
        **served_versions(root),
    }
    mismatched = {name: version for name, version in versions.items() if version != expected}
    if mismatched:
        print("release version mismatch: expected %s; found %s" % (expected, mismatched), file=sys.stderr)
        return 1
    head = alembic_head_revision(root)
    configured = expected_schema_revision(root)
    if head != configured:
        print(
            "database schema revision mismatch: migrations head is %s but the API expects %s"
            % (head, configured),
            file=sys.stderr,
        )
        return 1
    print("release version %s verified (schema revision %s)" % (expected, head))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
