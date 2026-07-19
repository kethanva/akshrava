#!/usr/bin/env python3
"""Mint a short-lived device JWT for volunteer provisioning; never commit its output."""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import jwt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    algorithm = os.environ.get("JWT_ALGORITHM", "RS256").upper()
    if algorithm == "RS256":
        key_file = os.environ.get("JWT_PRIVATE_KEY_FILE", "")
        if not key_file:
            sys.exit("Set JWT_PRIVATE_KEY_FILE before minting an RS256 token.")
        try:
            secret = open(key_file, encoding="utf-8").read()
        except OSError as exc:
            sys.exit("Unable to read JWT_PRIVATE_KEY_FILE: %s" % exc)
    elif algorithm == "HS256":
        secret = os.environ.get("JWT_SECRET")
        if not secret:
            sys.exit("Set JWT_SECRET before minting a token.")
    else:
        sys.exit("JWT_ALGORITHM must be HS256 or RS256.")
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": args.device_id,
            "aud": "akshrava-device",
            "iat": now,
            "exp": now + timedelta(days=args.days),
        },
        secret,
        algorithm=algorithm,
    )
    print(token)


if __name__ == "__main__":
    main()
