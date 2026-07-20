#!/usr/bin/env bash
# Android-path live E2E against Cloud Run (WSS + remote vision).
#
# - Checks livez/readyz
# - Mints RS256 device JWT from Secret Manager
# - Builds (+ installs) debug APK when a device/emulator is available
# - Runs instrumented GcpLiveProtocolClientE2eTest on device/emulator
#
# Usage:
#   GOOGLE_APPLICATION_CREDENTIALS=... ./scripts/e2e_android_gcp.sh [device_id]
#
# Optional env:
#   AKSHRAVA_BASE_URL, AKSHRAVA_WSS_URL, AKSHRAVA_CALIBRATION_ID
#   ANDROID_HOME / ANDROID_SDK_ROOT
#   AKSHRAVA_AVD (default: akshrava_api34_arm64)
#   AKSHRAVA_BOOT_EMULATOR=0 to skip auto-boot
#   AKSHRAVA_SKIP_INSTALL=1 to skip assemble/install (still runs connected test)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE_ID="${1:-android-gcp-e2e-$(date +%s)}"
BASE_URL="${AKSHRAVA_BASE_URL:-https://akshrava-api-c7d3j4nzdq-uc.a.run.app}"
WSS_URL="${AKSHRAVA_WSS_URL:-${BASE_URL/https/wss}/v1/session}"
CALIBRATION_ID="${AKSHRAVA_CALIBRATION_ID:-e2e-r0}"
AVD_NAME="${AKSHRAVA_AVD:-akshrava_api34_arm64}"
ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-${HOME}/Library/Android/sdk}}"
ADB="${ANDROID_HOME}/platform-tools/adb"
EMULATOR="${ANDROID_HOME}/emulator/emulator"
export ANDROID_HOME ANDROID_SDK_ROOT="$ANDROID_HOME"

export PATH="${HOME}/google-cloud-sdk/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/emulator:${PATH}"
: "${GOOGLE_APPLICATION_CREDENTIALS:?Set GOOGLE_APPLICATION_CREDENTIALS}"
: "${CLOUDSDK_CORE_PROJECT:=project-704ccb8e-8b12-4da6-a3f}"
export CLOUDSDK_CORE_PROJECT CLOUDSDK_CORE_DISABLE_PROMPTS=1
export CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="${GOOGLE_APPLICATION_CREDENTIALS}"

if [[ -x "${ROOT}/backend/.venv/bin/python" ]]; then
  PY="${ROOT}/backend/.venv/bin/python"
else
  PY="python3"
fi
"$PY" -c "import jwt" 2>/dev/null || "$PY" -m pip install -q 'PyJWT>=2.8'

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STARTED_EMULATOR=0

log() { printf '%s\n' "$*"; }

wait_for_adb_device() {
  local deadline=$((SECONDS + ${1:-180}))
  while (( SECONDS < deadline )); do
    if "$ADB" devices 2>/dev/null | awk 'NR>1 && $2=="device" {found=1} END{exit !found}'; then
      return 0
    fi
    sleep 2
  done
  return 1
}

boot_emulator_if_needed() {
  if [[ "${AKSHRAVA_BOOT_EMULATOR:-1}" != "1" ]]; then
    return 0
  fi
  if wait_for_adb_device 5; then
    log "==> using already-connected adb device"
    return 0
  fi
  if [[ ! -x "$EMULATOR" ]]; then
    log "ERROR: no adb device and emulator binary missing at $EMULATOR"
    return 1
  fi
  if ! "$EMULATOR" -list-avds 2>/dev/null | grep -qx "$AVD_NAME"; then
    log "ERROR: AVD '$AVD_NAME' not found. Create with:"
    log "  avdmanager create avd -n $AVD_NAME -k 'system-images;android-34;google_apis;arm64-v8a' -d pixel_6"
    return 1
  fi
  log "==> booting emulator AVD=${AVD_NAME}"
  nohup "$EMULATOR" -avd "$AVD_NAME" \
    -no-snapshot-save -no-audio -no-boot-anim -gpu swiftshader_indirect \
    -netdelay none -netspeed full \
    >"${TMP}/emulator.log" 2>&1 &
  STARTED_EMULATOR=1
  if ! wait_for_adb_device 240; then
    log "ERROR: emulator did not appear in adb devices"
    tail -50 "${TMP}/emulator.log" || true
    return 1
  fi
  log "==> waiting for boot completed"
  local deadline=$((SECONDS + 240))
  while (( SECONDS < deadline )); do
    local booted
    booted="$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)"
    if [[ "$booted" == "1" ]]; then
      "$ADB" wait-for-device
      # Give Package Manager a moment after boot.
      sleep 5
      return 0
    fi
    sleep 3
  done
  log "ERROR: emulator boot_completed never became 1"
  tail -50 "${TMP}/emulator.log" || true
  return 1
}

log "==> livez"
curl -fsS -m 20 "${BASE_URL}/livez" | tee "${TMP}/livez.json"
echo
log "==> readyz"
curl -fsS -m 20 "${BASE_URL}/readyz" | tee "${TMP}/readyz.json"
echo

log "==> mint Android device token (${DEVICE_ID})"
gcloud secrets versions access latest --secret=akshrava-jwt-private >"${TMP}/jwt-private.pem"
chmod 600 "${TMP}/jwt-private.pem"
export JWT_ALGORITHM=RS256 JWT_PRIVATE_KEY_FILE="${TMP}/jwt-private.pem"
TOKEN="$("$PY" "${ROOT}/scripts/mint_device_token.py" "${DEVICE_ID}" --days 1)"
log "token_len=${#TOKEN}"

boot_emulator_if_needed

SERIAL="$("$ADB" devices | awk 'NR>1 && $2=="device" {print $1; exit}')"
if [[ -z "$SERIAL" ]]; then
  log "ERROR: no adb device/emulator available after best effort"
  exit 1
fi
log "==> adb device serial=${SERIAL}"

if [[ "${AKSHRAVA_SKIP_INSTALL:-0}" != "1" ]]; then
  log "==> assembleDebug + installDebug"
  (
    cd "${ROOT}/android"
    ./gradlew --no-daemon assembleDebug installDebug
  )
else
  log "==> skip install (AKSHRAVA_SKIP_INSTALL=1)"
fi

log "==> connectedAndroidTest GcpLiveProtocolClientE2eTest"
(
  cd "${ROOT}/android"
  ./gradlew --no-daemon connectedDebugAndroidTest \
    -Pandroid.testInstrumentationRunnerArguments.akshrava_test_token="${TOKEN}" \
    -Pandroid.testInstrumentationRunnerArguments.akshrava_wss_url="${WSS_URL}" \
    -Pandroid.testInstrumentationRunnerArguments.akshrava_calibration_id="${CALIBRATION_ID}" \
    -Pandroid.testInstrumentationRunnerArguments.class=org.akshrava.app.GcpLiveProtocolClientE2eTest
)

log "==> ANDROID_GCP_E2E_PASS device=${SERIAL} wss=${WSS_URL} vision_enabled=true"
log "    Handset-only gap: camera/TTS/provisioning UX on a physical donated phone."
exit 0
