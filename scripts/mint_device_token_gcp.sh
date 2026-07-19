#!/usr/bin/env bash
# Fetch the provisioning RS256 private key from Secret Manager, mint a device JWT, shred the key.
set -euo pipefail

DEVICE_ID="${1:?usage: $0 <device_id> [days]}"
DAYS="${2:-30}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

gcloud secrets versions access latest --secret=akshrava-jwt-private >"$TMP/jwt-private.pem"
chmod 600 "$TMP/jwt-private.pem"

export JWT_ALGORITHM=RS256
export JWT_PRIVATE_KEY_FILE="$TMP/jwt-private.pem"
python3 "$ROOT/scripts/mint_device_token.py" "$DEVICE_ID" --days "$DAYS"
