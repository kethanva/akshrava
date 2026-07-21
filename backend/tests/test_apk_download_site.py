import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_apk_download_site_links_every_supported_api_to_the_signed_universal_apk(tmp_path):
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_apk_download_site.py"),
            "--tag",
            "v0.2.12",
            "--repository",
            "kethanva/akshrava",
            "--apk-name",
            "Akshrava-0.2.12-universal.apk",
            "--sha256",
            "a" * 64,
            "--out",
            str(tmp_path),
        ],
        check=True,
    )
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    url = "https://github.com/kethanva/akshrava/releases/download/v0.2.12/Akshrava-0.2.12-universal.apk"
    assert page.count(url) == 12
    assert "API 26" in page
    assert "API 36" in page
    assert "Android 16" in page
    assert "Release smoke tested" in page
    assert "Legacy release smoke tested; device qualification required" in page
