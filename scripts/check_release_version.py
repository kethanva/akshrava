#!/usr/bin/env python3
"""Ensure a vX.Y.Z tag names the exact backend and Android release version."""

import re
import sys
from pathlib import Path


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


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", sys.argv[1]):
        print("usage: check_release_version.py vX.Y.Z", file=sys.stderr)
        return 2
    expected = sys.argv[1][1:]
    root = Path(__file__).resolve().parents[1]
    versions = {"backend": version_from_backend(root), "android": version_from_android(root)}
    mismatched = {name: version for name, version in versions.items() if version != expected}
    if mismatched:
        print("release version mismatch: expected %s; found %s" % (expected, mismatched), file=sys.stderr)
        return 1
    print("release version %s verified" % expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
