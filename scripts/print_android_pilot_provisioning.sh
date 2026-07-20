#!/usr/bin/env bash
# Print volunteer provisioning values for the live GCP pilot (WSS + RS256 device token).
# Usage: GOOGLE_APPLICATION_CREDENTIALS=... ./scripts/print_android_pilot_provisioning.sh [device_id] [days]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE_ID="${1:-android-pilot-$(date +%s)}"
DAYS="${2:-1}"
WSS_URL="${AKSHRAVA_WSS_URL:-wss://<your-cloud-run-endpoint>/v1/session}"
CALIBRATION_ID="${AKSHRAVA_CALIBRATION_ID:-e2e-r0}"

: "${GOOGLE_APPLICATION_CREDENTIALS:?Set GOOGLE_APPLICATION_CREDENTIALS to the deploy SA JSON}"

TOKEN="$("$ROOT/scripts/mint_device_token_gcp.sh" "$DEVICE_ID" "$DAYS")"

cat <<EOF
=== Akshrava Android pilot provisioning ===
Enter these in the volunteer provisioning screen:

  WSS endpoint:     ${WSS_URL}
  Device token:     ${TOKEN}
  Calibration ID:   ${CALIBRATION_ID}
  Device ID (claim): ${DEVICE_ID}
  Token TTL days:   ${DAYS}

APK (debug): ${ROOT}/android/app/build/outputs/apk/debug/app-debug.apk
EOF
