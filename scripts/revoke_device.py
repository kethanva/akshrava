#!/usr/bin/env python3
"""Revoke one provisioned device ID immediately using the configured database."""

import asyncio
import os
import sys

from akshrava_backend.storage import Store


async def main(device_id: str) -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    store = Store(url)
    try:
        await store.initialize()
        if not await store.revoke_device(device_id):
            print("device was not found", file=sys.stderr)
            return 1
        print("device revoked: %s" % device_id)
        return 0
    finally:
        await store.engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: revoke_device.py DEVICE_ID", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1].strip())))
