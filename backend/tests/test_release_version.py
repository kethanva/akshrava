import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_release_version_script_accepts_the_current_v010_tag():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_release_version.py"), "v0.1.0"],
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
