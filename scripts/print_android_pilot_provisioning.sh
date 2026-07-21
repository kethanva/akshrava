#!/usr/bin/env bash
# Print volunteer provisioning values for the live GCP pilot (WSS + RS256 device token).
# Usage: GOOGLE_APPLICATION_CREDENTIALS=... ./scripts/print_android_pilot_provisioning.sh [device_id] [days]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
DEVICE_ID="${1:-android-pilot-$(date +%s)}"
DAYS="${2:-1}"
# Resolve the live WSS endpoint: explicit override → terraform output → known Cloud Run default.
# The old <your-cloud-run-endpoint> placeholder produced provisioning cards that could never
# reach GCP, silently stranding volunteer phones.
TF_WSS_URL=""
if command -v terraform &>/dev/null && [ -d "$ROOT/cloud/gcp" ]; then
  TF_WSS_URL="$(terraform -chdir="$ROOT/cloud/gcp" output -raw websocket_url 2>/dev/null || true)"
fi
BASE_URL="${AKSHRAVA_BASE_URL:-https://akshrava-api-c7d3j4nzdq-uc.a.run.app}"
WSS_URL="${AKSHRAVA_WSS_URL:-${TF_WSS_URL:-${BASE_URL/https/wss}/v1/session}}"
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
