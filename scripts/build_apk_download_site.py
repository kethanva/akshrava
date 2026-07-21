#!/usr/bin/env python3
"""Create the static GitHub Pages download site for one signed Android release."""

from __future__ import annotations

import argparse
import html
from pathlib import Path


SUPPORTED_APIS = tuple(range(26, 37))
RELEASE_VALIDATION_APIS = tuple(range(28, 37))
ANDROID_VERSION_BY_API = {
    26: "Android 8",
    27: "Android 8.1",
    28: "Android 9",
    29: "Android 10",
    30: "Android 11",
    31: "Android 12",
    32: "Android 12L",
    33: "Android 13",
    34: "Android 14",
    35: "Android 15",
    36: "Android 16",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="Release tag, for example v0.2.12")
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--apk-name", required=True, help="Published APK asset name")
    parser.add_argument("--sha256", required=True, help="APK SHA-256")
    parser.add_argument("--out", required=True, type=Path, help="Output directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.tag.startswith("v") or "/" not in args.repository:
        raise SystemExit("--tag must start with v and --repository must be owner/repository")
    if len(args.sha256) != 64 or any(ch not in "0123456789abcdef" for ch in args.sha256.lower()):
        raise SystemExit("--sha256 must be a 64-character hexadecimal digest")

    download_url = (
        f"https://github.com/{args.repository}/releases/download/{args.tag}/{args.apk_name}"
    )
    rows = "\n".join(
        f"<tr><td>{ANDROID_VERSION_BY_API[api]}</td><td>API {api}</td><td>"
        f"{'Release smoke tested' if api in RELEASE_VALIDATION_APIS else 'Legacy release smoke tested; device qualification required'}</td>"
        f"<td><a href=\"{html.escape(download_url, quote=True)}\">Download universal APK</a></td></tr>"
        for api in SUPPORTED_APIS
    )
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "index.html").write_text(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Akshrava {html.escape(args.tag)} downloads</title>
<style>body{{font:16px system-ui,sans-serif;max-width:900px;margin:3rem auto;padding:0 1rem;line-height:1.5}}a{{color:#0759b8}}.download{{display:inline-block;background:#0759b8;color:#fff;padding:.8rem 1rem;border-radius:.4rem;text-decoration:none;font-weight:700}}table{{border-collapse:collapse;width:100%;margin-top:1rem}}td,th{{border:1px solid #d0d7de;padding:.6rem;text-align:left}}code{{overflow-wrap:anywhere}}</style>
</head><body><main><h1>Akshrava {html.escape(args.tag)}</h1>
<p>This is one signed universal APK. It supports Android 8 through Android 16 (API 26–36); download the same verified release for your supported device.</p>
<p><a class="download" href="{html.escape(download_url, quote=True)}">Download Android APK</a></p>
<p>SHA-256: <code>{html.escape(args.sha256)}</code></p>
<p>Verify the checksum before installing. A release build is an engineering artifact, not authorization for unsupervised mobility use.</p>
<h2>Compatibility</h2><table><thead><tr><th>Android</th><th>API</th><th>CI coverage</th><th>APK</th></tr></thead><tbody>{rows}</tbody></table>
<p><a href="https://github.com/{html.escape(args.repository, quote=True)}/releases/tag/{html.escape(args.tag, quote=True)}">Release notes and all checksums</a></p>
</main></body></html>""",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
