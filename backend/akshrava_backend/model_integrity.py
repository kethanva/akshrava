"""Release-gate checks for model artifacts.

Model files are safety artifacts, not cache entries. When a detector is enabled outside a local
test, the mounted file must match the SHA-256 recorded in the release/deployment record.
"""

import hashlib
from pathlib import Path


def verify_model_sha256(path: str, expected_sha256: str, *, required: bool) -> str:
    model_path = Path(path)
    if not model_path.is_file():
        raise RuntimeError("YOLO_WEIGHTS must name a local, baked-in or read-only mounted model file")
    expected = expected_sha256.strip().lower()
    if not expected:
        if required:
            raise RuntimeError("YOLO_WEIGHTS_SHA256 is required when activating a detector outside development")
        return ""
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise RuntimeError("YOLO_WEIGHTS_SHA256 must be a lowercase 64-character SHA-256 hex digest")

    digest = hashlib.sha256()
    with model_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError("YOLO_WEIGHTS_SHA256 does not match the mounted model file")
    return actual
