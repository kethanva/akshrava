#!/usr/bin/env python3
"""Upsert a mount/camera calibration profile. Verified profiles are required for range_valid."""

import argparse
import asyncio
import os
import sys

from akshrava_backend.storage import Store


async def upsert(
    calibration_id: str,
    focal_px: float,
    camera_height_m: float,
    *,
    verified: bool,
) -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    store = Store(url, bootstrap_schema=url.startswith("sqlite"))
    try:
        await store.initialize()
        await store.upsert_calibration_profile(
            calibration_id,
            focal_px,
            camera_height_m,
            verified=verified,
        )
        profile = await store.geometry_profile(calibration_id)
        if verified and profile is None:
            raise SystemExit("profile written but geometry_profile still returns None")
        state = "verified" if verified else "unverified (fail-closed for range)"
        print(
            "calibration_id=%s focal_px=%s height_m=%s %s"
            % (calibration_id, focal_px, camera_height_m, state)
        )
    finally:
        await store.engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calibration_id")
    parser.add_argument("--focal-px", type=float, required=True)
    parser.add_argument("--camera-height-m", type=float, required=True)
    parser.add_argument(
        "--confirm-verified",
        action="store_true",
        help="Mark the profile verified after controlled-course sign-off. Required for range_valid.",
    )
    args = parser.parse_args()
    if args.focal_px <= 0 or args.camera_height_m <= 0:
        print("focal-px and camera-height-m must be positive", file=sys.stderr)
        return 2
    asyncio.run(
        upsert(
            args.calibration_id.strip(),
            args.focal_px,
            args.camera_height_m,
            verified=args.confirm_verified,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
