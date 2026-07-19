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
    parser.add_argument(
        "--diagnostic-consent",
        action="store_true",
        help="Embed diagnostic_consent=true (blocked unless DIAGNOSTIC_UPLOADS_ENABLED=true and blur exists).",
    )
    parser.add_argument(
        "--force-unsafe-diagnostic-consent",
        action="store_true",
        help="Lab-only: mint diagnostic_consent without DIAGNOSTIC_UPLOADS_ENABLED (uploads still blocked server-side).",
    )
    args = parser.parse_args()
    if args.diagnostic_consent:
        uploads_enabled = os.environ.get("DIAGNOSTIC_UPLOADS_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        if not uploads_enabled and not args.force_unsafe_diagnostic_consent:
            sys.exit(
                "Refusing --diagnostic-consent: face/plate blur is not ready. "
                "Set DIAGNOSTIC_UPLOADS_ENABLED=true only after PRIVACY.md blur gate, "
                "or pass --force-unsafe-diagnostic-consent for lab tokens (API still will not upload)."
            )
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
    claims = {
        "sub": args.device_id,
        "aud": "akshrava-device",
        "iat": now,
        "exp": now + timedelta(days=args.days),
    }
    if args.diagnostic_consent:
        claims["diagnostic_consent"] = True
    token = jwt.encode(
        claims,
        secret,
        algorithm=algorithm,
    )
    print(token)


if __name__ == "__main__":
    main()
