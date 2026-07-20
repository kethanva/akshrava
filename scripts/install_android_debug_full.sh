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
log "==> Checking backend health (advisory only)..."
HTTP_BASE="https://${WSS_URL#wss://}"
HTTP_BASE="${HTTP_BASE%/v1/session}"
command -v curl >/dev/null || { log "⚠️  curl not found; skipping health check"; HTTP_BASE=""; }
if [ -n "$HTTP_BASE" ]; then
  HEALTH_OK=true
  for endpoint in "livez" "readyz"; do
    RESPONSE=$(curl -s -w "\n%{http_code}" --connect-timeout 5 --max-time 10 "$HTTP_BASE/$endpoint" 2>/dev/null || echo -e "\n000")
    STATUS=$(echo "$RESPONSE" | tail -1)
    if [ -z "$STATUS" ] || [ "$STATUS" = "000" ]; then
      log "⚠️  $endpoint: unreachable (network or DNS issue)"
      HEALTH_OK=false
    elif [ "$STATUS" != "200" ]; then
      log "⚠️  $endpoint: returned $STATUS"
      HEALTH_OK=false
    else
      log "✓ $endpoint: 200"
    fi
  done
  if [ "$HEALTH_OK" = "false" ]; then
    log "Backend may be unreachable. Continuing anyway (may fail later)..."
  else
    log "Backend health: OK"
  fi
fi

# ── Mint device token ───────────────────────────────────────────────────────
log "==> Minting RS256 device token..."
TOKEN=$("$REPO_ROOT/scripts/mint_device_token_gcp.sh" "$DEVICE_ID" "$TOKEN_TTL_DAYS" 2>/dev/null)
[ -n "$TOKEN" ] || die "Failed to mint device token"
log "Token minted (len=${#TOKEN})"

# ── Build debug APK + androidTest APK ────────────────────────────────────────
# We build both here, but deliberately do NOT run `./gradlew connectedDebugAndroidTest`:
# that Gradle task uninstalls the target app (and wipes its Keystore-provisioned data)
# as part of its own test-run cleanup, which silently undid provisioning. Instead we
# install both APKs ourselves and drive instrumentation with `adb shell am instrument`,
# which does not uninstall anything afterward.
log "==> Building debug APK + test APK..."
cd "$REPO_ROOT/android"
BUILD_LOG=$(mktemp)
if ! ./gradlew --no-daemon assembleDebug assembleDebugAndroidTest >"$BUILD_LOG" 2>&1; then
  tail -40 "$BUILD_LOG" >&2
  rm -f "$BUILD_LOG"
  die "Gradle build failed (see output above)"
fi
rm -f "$BUILD_LOG"
APK="$REPO_ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
TEST_APK="$REPO_ROOT/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
[ -f "$APK" ] || die "APK not found at $APK"
[ -f "$TEST_APK" ] || die "Test APK not found at $TEST_APK"
log "APK built: $(du -sh "$APK" | awk '{print $1}')"

# ── Install both APKs ─────────────────────────────────────────────────────────
log "==> Installing app APK on $DEVICE_SERIAL..."
INSTALL_OUT=$(adb -s "$DEVICE_SERIAL" install -r -t -d "$APK" 2>&1)
echo "$INSTALL_OUT" | grep -q "^Success" || die "APK install failed:
$INSTALL_OUT"
log "==> Installing test APK on $DEVICE_SERIAL..."
TEST_INSTALL_OUT=$(adb -s "$DEVICE_SERIAL" install -r -t "$TEST_APK" 2>&1)
echo "$TEST_INSTALL_OUT" | grep -q "^Success" || die "Test APK install failed:
$TEST_INSTALL_OUT"
adb -s "$DEVICE_SERIAL" shell pm list packages | grep -q "org.akshrava.app$" || die "App package missing after install"
log "APKs installed and verified present on device"

# ── Provision via am instrument (no gradle uninstall side effect) ───────────
log "==> Provisioning Keystore (endpoint + token + calibration)..."
INSTRUMENT_OUT=$(adb -s "$DEVICE_SERIAL" shell am instrument -w -r \
  -e akshrava_test_token "$TOKEN" \
  -e akshrava_wss_url "$WSS_URL" \
  -e akshrava_calibration_id "$CALIBRATION_ID" \
  -e akshrava_provision_target true \
  -e class org.akshrava.app.GcpLiveProvisioningTest \
  org.akshrava.app.test/androidx.test.runner.AndroidJUnitRunner 2>&1)
if ! echo "$INSTRUMENT_OUT" | grep -q "OK ("; then
  echo "$INSTRUMENT_OUT" >&2
  die "Provisioning instrumentation failed (see output above)"
fi
# Confirm the app is still installed (am instrument never uninstalls, but verify anyway).
adb -s "$DEVICE_SERIAL" shell pm list packages | grep -q "org.akshrava.app$" || die "App package missing after provisioning"
log "Keystore provisioned and app confirmed installed"

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
