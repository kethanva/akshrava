#!/usr/bin/env python3
"""Soak a single walking session against a live backend and report when it dies.

Reproduces what the phone actually does over a long walk — a frame roughly every 833 ms plus
a 60 s application-level ping — and records every disconnect, error frame and stall. The point
is to separate "the server cannot hold a long session" from "the phone stops sending", which
logs alone cannot distinguish.

Usage:
  AKSHRAVA_WSS_URL=wss://host/v1/session AKSHRAVA_TOKEN=<jwt> \
    python3 scripts/soak_session.py --minutes 15

  # or let it mint a token itself (needs gcloud + Secret Manager access)
  python3 scripts/soak_session.py --minutes 15 --mint

Exit code is 0 only if the session survived the full duration without an unexpected close.
"""

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time

import websockets

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Valid 64x64 JPEG, same fixture the instrumentation tests use.
FIXTURE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8f"
    "ExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4e"
    "Hh7/wAARCABAAEADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQID"
    "AAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlq"
    "c3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3"
    "+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEI"
    "FEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImK"
    "kpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDm"
    "qKKK/VT80CiiigAooooAKKKKACiiigBRRigUtfl3EfEeZYPMqlChUtFWsrRe8U+qvufunBvBuS5lktHFYqjzTlzXfNJbSklopJbJdBMU"
    "GlpDRw5xHmWMzKnQr1Lxd7q0VtFvor7hxlwbkuW5LWxWFo8s48tnzSe8op6OTWzfQSiiiv1E/CwooooAUUtIKM1+XcR8OZljMyqV6FO8"
    "XazvFbRS6u+5+6cG8ZZLluS0cLiq3LOPNdcsnvKTWqi1s11FpDRmg0cOcOZlg8yp169O0Ve7vF7xa6O+4cZcZZLmWS1sLha3NOXLZcsl"
    "tKLerilsn1Eooor9RPwsKKKKACiiigAooooAKKKKAP/Z"
)

FRAME_INTERVAL_S = 0.833   # ~1.2 FPS, the app's normal walking cadence
PING_INTERVAL_S = 60.0     # ProtocolClient.APP_PING_INTERVAL_MS
# Longer than the server's slowest configured inference budget, so a slow CPU frame is not
# mistaken for a dead session.
RESULT_TIMEOUT_S = 30.0


def mint_token(device_id, days=1):
    script = os.path.join(REPO_ROOT, "scripts", "mint_device_token_gcp.sh")
    return subprocess.check_output([script, device_id, str(days)], text=True).strip()


class Stats:
    def __init__(self):
        self.frames_sent = 0
        self.results = 0
        self.errors = {}
        self.quality_updates = 0
        self.pongs = 0
        self.max_gap_s = 0.0
        self.detections_seen = 0

    def note_error(self, code):
        self.errors[code] = self.errors.get(code, 0) + 1


async def soak(url, token, minutes, calibration_id, verbose, idle_only=False):
    deadline = time.monotonic() + minutes * 60
    stats = Stats()
    started = time.monotonic()
    frame_id = 0
    last_ping = time.monotonic()

    def elapsed():
        return time.monotonic() - started

    # open_timeout covers Cloud Run cold start; ping_interval=None because the server speaks an
    # application-level ping and we do not want the library's protocol ping to mask a stall.
    # websockets renamed extra_headers -> additional_headers in 14.0. The mismatch only raises
    # when the connection is awaited, not when it is constructed, so pick by signature.
    import inspect

    header_kwarg = (
        "additional_headers"
        if "additional_headers" in inspect.signature(websockets.connect).parameters
        else "extra_headers"
    )
    connect_kwargs = {
        header_kwarg: {"Authorization": f"Bearer {token}"},
        "open_timeout": 45,
        "ping_interval": None,
        "max_size": 4 * 1024 * 1024,
    }
    if url.startswith("wss://"):
        # Verify against certifi's bundle. Some Python builds (notably python.org on macOS) ship
        # without a usable system trust store, and the failure mode there is a tempting
        # "just disable verification" — which would make this soak prove nothing about TLS.
        import ssl

        try:
            import certifi

            connect_kwargs["ssl"] = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            connect_kwargs["ssl"] = ssl.create_default_context()

    async with websockets.connect(url, **connect_kwargs) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))
        if ready.get("type") != "ready":
            raise RuntimeError(f"expected ready, got {ready}")
        print(f"[{elapsed():7.1f}s] ready detector={ready.get('detector')} "
              f"vision_enabled={ready.get('vision_enabled')}")
        if not ready.get("vision_enabled"):
            raise RuntimeError("backend reports vision_enabled=false; a phone would refuse to stream")

        if idle_only:
            # A stationary user's frames are all duplicate-dropped on the phone, so the ONLY
            # traffic holding the server's 180 s admission lease open is this ping. If the lease
            # is not actually renewed by control traffic, the session dies around the three
            # minute mark — exactly the reported symptom — and streaming soaks never show it
            # because their frames renew the lease as a side effect.
            while time.monotonic() < deadline:
                await asyncio.sleep(PING_INTERVAL_S)
                await ws.send(json.dumps({"type": "ping"}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=RESULT_TIMEOUT_S))
                if reply.get("type") != "pong":
                    raise RuntimeError(f"expected pong, got {reply}")
                stats.pongs += 1
                print(f"[{elapsed():7.1f}s] pong {stats.pongs} — session still admitted")
            return stats, elapsed()

        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_ping >= PING_INTERVAL_S:
                last_ping = now
                await ws.send(json.dumps({"type": "ping"}))

            frame_id += 1
            capture_mono = int(now * 1000)
            header = {
                "type": "frame", "id": frame_id, "capture_mono_ms": capture_mono,
                "capture_epoch_ms": int(time.time() * 1000),
                "w": 64, "h": 64, "jpeg_bytes": len(FIXTURE_JPEG),
                "camera_calibration_id": calibration_id,
                "mode": "normal", "priority": False, "language": "en",
                "trace_id": f"soak-{frame_id}",
            }
            sent_at = time.monotonic()
            await ws.send(json.dumps(header))
            await ws.send(FIXTURE_JPEG)
            stats.frames_sent += 1

            # Drain until this frame resolves; the server also emits a quality hint per result.
            settled = False
            while not settled:
                raw = await asyncio.wait_for(ws.recv(), timeout=RESULT_TIMEOUT_S)
                msg = json.loads(raw) if isinstance(raw, str) else {}
                kind = msg.get("type")
                if kind == "result":
                    stats.results += 1
                    stats.detections_seen += msg.get("detection_count") or 0
                    settled = True
                elif kind == "error":
                    stats.note_error(msg.get("code", "?"))
                    settled = True  # soft errors free the slot; the phone retries next frame
                elif kind == "quality":
                    stats.quality_updates += 1
                elif kind == "pong":
                    stats.pongs += 1
                    settled = False

            gap = time.monotonic() - sent_at
            stats.max_gap_s = max(stats.max_gap_s, gap)

            if verbose and stats.frames_sent % 20 == 0:
                print(f"[{elapsed():7.1f}s] frames={stats.frames_sent} results={stats.results} "
                      f"errors={stats.errors} max_rtt={stats.max_gap_s:.2f}s")

            await asyncio.sleep(max(0.0, FRAME_INTERVAL_S - gap))

    return stats, elapsed()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=15.0)
    parser.add_argument("--calibration-id", default=os.environ.get("AKSHRAVA_CALIBRATION_ID", "e2e-r0"))
    parser.add_argument("--mint", action="store_true", help="Mint a device token via gcloud")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--idle-only", action="store_true",
                        help="Send no frames; only the 60s ping. Tests lease renewal for a stationary user.")
    args = parser.parse_args()

    url = os.environ.get("AKSHRAVA_WSS_URL")
    if not url:
        sys.exit("Set AKSHRAVA_WSS_URL")
    token = os.environ.get("AKSHRAVA_TOKEN", "")
    if args.mint or not token:
        token = mint_token(f"soak-{int(time.time())}")

    print(f"Soaking {url} for {args.minutes} minutes "
          f"(~{int(args.minutes * 60 / FRAME_INTERVAL_S)} frames)")
    try:
        stats, ran_for = await soak(url, token, args.minutes, args.calibration_id,
                                    not args.quiet, idle_only=args.idle_only)
    except Exception as exc:
        print(f"\nFAILED after start: {type(exc).__name__}: {exc}")
        sys.exit(1)

    print("\n==================== SOAK RESULT ====================")
    print(f"  duration        {ran_for / 60:.2f} min")
    print(f"  frames sent     {stats.frames_sent}")
    print(f"  results         {stats.results}")
    print(f"  quality hints   {stats.quality_updates}")
    print(f"  pongs           {stats.pongs}")
    print(f"  detections      {stats.detections_seen}")
    print(f"  slowest frame   {stats.max_gap_s:.2f}s")
    print(f"  errors          {stats.errors or 'none'}")
    survived = ran_for >= args.minutes * 60 * 0.98
    print(f"  survived        {'YES' if survived else 'NO'}")
    print("=====================================================")
    sys.exit(0 if survived else 1)


if __name__ == "__main__":
    asyncio.run(main())
