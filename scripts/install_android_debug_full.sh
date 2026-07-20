#!/usr/bin/env bash
# install_android_debug_full.sh — Complete end-to-end provisioning for debug Android app.
#
# Automates: token mint → APK build → APK install → Keystore provisioning → ready-to-stream.
# A phone is fully configured and can stream frames to GCP after this script completes.
#
# Usage:
#   GOOGLE_APPLICATION_CREDENTIALS=<sa.json> ./scripts/install_android_debug_full.sh [device_serial]
#
# Optional env (or set in .env at repo root):
#   AKSHRAVA_BASE_URL        Cloud Run HTTPS base (default: akshrava-api-c7d3j4nzdq-uc.a.run.app)
#   AKSHRAVA_WSS_URL         Override the full WSS endpoint (base-url derivation skipped if set)
#   AKSHRAVA_CALIBRATION_ID  Calibration profile (default: e2e-r0)
#   AKSHRAVA_DEVICE_ID       Device identifier for JWT (default: adb-<serial>-<timestamp>)
#   AKSHRAVA_TOKEN_TTL_DAYS  Token validity (default: 30)

set -euo pipefail

log() { echo "[$(date '+%H:%M:%S')] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$REPO_ROOT/.env" ]; then set -a; source "$REPO_ROOT/.env"; set +a; fi

# ── Precondition checks ──────────────────────────────────────────────────────
: "${GOOGLE_APPLICATION_CREDENTIALS:?Set GOOGLE_APPLICATION_CREDENTIALS to deploy SA JSON}"
[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ] || die "GOOGLE_APPLICATION_CREDENTIALS not readable: $GOOGLE_APPLICATION_CREDENTIALS"

export PATH="$HOME/google-cloud-sdk/bin:${ANDROID_HOME:-$HOME/Library/Android/sdk}/platform-tools:${ANDROID_HOME:-$HOME/Library/Android/sdk}/emulator:$PATH"

command -v gcloud >/dev/null || die "gcloud not found; install Google Cloud SDK"
command -v adb >/dev/null || die "adb not found; set ANDROID_HOME or install Android SDK"
command -v python3 >/dev/null || die "python3 not found"

# ── Resolve device serial ────────────────────────────────────────────────────
DEVICE_SERIAL="${1:-}"
if [ -z "$DEVICE_SERIAL" ]; then
  log "==> Scanning for connected Android devices..."
  DEVICES=$(adb devices | awk 'NR>1 && $2=="device" {print $1}')
  DEVICE_COUNT=$(echo "$DEVICES" | grep -c . || true)
  if [ "$DEVICE_COUNT" -eq 0 ]; then
    die "No authorized Android devices found. Enable USB Debugging and authorize on device."
  elif [ "$DEVICE_COUNT" -gt 1 ]; then
    die "Multiple devices found. Pass serial: ./scripts/install_android_debug_full.sh <serial>"
  fi
  DEVICE_SERIAL=$(echo "$DEVICES" | head -1)
fi
log "Device: $DEVICE_SERIAL"

# ── Resolve identifiers ──────────────────────────────────────────────────────
DEVICE_ID="${AKSHRAVA_DEVICE_ID:-adb-${DEVICE_SERIAL}-$(date +%s)}"
TOKEN_TTL_DAYS="${AKSHRAVA_TOKEN_TTL_DAYS:-30}"
CALIBRATION_ID="${AKSHRAVA_CALIBRATION_ID:-e2e-r0}"

log "Device ID: $DEVICE_ID"
log "Calibration: $CALIBRATION_ID"
log "Token TTL: $TOKEN_TTL_DAYS days"

# ── Resolve GCP WSS endpoint ─────────────────────────────────────────────────
TF_WSS_URL=""
if command -v terraform >/dev/null 2>&1 && [ -d "$REPO_ROOT/gcp" ]; then
  TF_WSS_URL="$(terraform -chdir="$REPO_ROOT/gcp" output -raw websocket_url 2>/dev/null || true)"
fi
BASE_URL="${AKSHRAVA_BASE_URL:-https://akshrava-api-c7d3j4nzdq-uc.a.run.app}"
WSS_URL="${AKSHRAVA_WSS_URL:-${TF_WSS_URL:-${BASE_URL/https/wss}/v1/session}}"
log "WSS Endpoint: $WSS_URL"
export AKSHRAVA_WSS_URL="$WSS_URL"

# ── Health check ─────────────────────────────────────────────────────────────
log "==> Checking backend health..."
HTTP_BASE="${WSS_URL/wss:\/\//https:\/\/}"
HTTP_BASE="${HTTP_BASE%/v1/session}"
for endpoint in "livez" "readyz"; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HTTP_BASE/$endpoint" || true)
  if [ "$STATUS" != "200" ]; then
    die "Backend $endpoint returned $STATUS (expected 200)"
  fi
done
log "Backend health: OK"

# ── Mint device token ───────────────────────────────────────────────────────
log "==> Minting RS256 device token..."
TOKEN=$("$REPO_ROOT/scripts/mint_device_token_gcp.sh" "$DEVICE_ID" "$TOKEN_TTL_DAYS" 2>/dev/null)
[ -n "$TOKEN" ] || die "Failed to mint device token"
log "Token minted (len=${#TOKEN})"

# ── Build debug APK ─────────────────────────────────────────────────────────
log "==> Building debug APK..."
cd "$REPO_ROOT/android"
./gradlew --no-daemon assembleDebug >/dev/null 2>&1 || die "Gradle build failed"
APK="$REPO_ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
[ -f "$APK" ] || die "APK not found at $APK"
log "APK built: $(du -sh "$APK" | awk '{print $1}')"

# ── Install APK ─────────────────────────────────────────────────────────────
log "==> Installing APK on $DEVICE_SERIAL..."
adb -s "$DEVICE_SERIAL" install -r -t -d "$APK" >/dev/null 2>&1 || die "APK install failed"
log "APK installed"

# ── Provision via instrumented test ─────────────────────────────────────────
log "==> Provisioning Keystore (endpoint + token + calibration)..."
cd "$REPO_ROOT/android"
./gradlew --no-daemon \
  connectedDebugAndroidTest \
  -Pandroid.testInstrumentationRunnerArguments.akshrava_test_token="$TOKEN" \
  -Pandroid.testInstrumentationRunnerArguments.akshrava_wss_url="$WSS_URL" \
  -Pandroid.testInstrumentationRunnerArguments.akshrava_calibration_id="$CALIBRATION_ID" \
  -Pandroid.testInstrumentationRunnerArguments.akshrava_provision_target=true \
  -Pandroid.testInstrumentationRunnerArguments.class=org.akshrava.app.GcpLiveProvisioningTest \
  >/dev/null 2>&1 || die "Provisioning test failed"
log "Keystore provisioned"

# ── Success ──────────────────────────────────────────────────────────────────
cat <<EOF

============================================================
 ✅ Android Debug Build — End-to-End Ready
============================================================

Device:            $DEVICE_SERIAL
Device ID:         $DEVICE_ID
Calibration:       $CALIBRATION_ID
WSS Endpoint:      $WSS_URL
Token TTL:         $TOKEN_TTL_DAYS days

Next: Launch the app's "Start" button (MainActivity) to begin
       streaming frames to GCP remote vision.

Logs:  adb -s $DEVICE_SERIAL logcat -s \\
       AssistService:* MainActivity:* ProtocolClient:* AndroidRuntime:* 2>&1

============================================================
EOF
