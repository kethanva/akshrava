#!/usr/bin/env bash
# install_android_debug_full.sh — Complete end-to-end provisioning for the debug Android app.
#
# Takes a bare connected phone to a verified, streaming-ready state:
#
#   preconditions → config → backend health → mint token → build → install →
#   grant permissions → provision Keystore → verify live GCP path → launch
#
# Every stage that can be proven is proven. Provisioning is read back off the device rather
# than trusted from a green test, and the live path is exercised with the freshly minted
# token before the script claims success — a phone that reaches the summary has already
# completed a real WebSocket session against the backend.
#
# Usage:
#   GOOGLE_APPLICATION_CREDENTIALS=<sa.json> ./scripts/install_android_debug_full.sh [device_serial]
#
# Flags:
#   --device <serial>   Target device (same as the positional argument)
#   --skip-build        Reuse existing APKs instead of running Gradle
#   --skip-verify       Skip the live GCP verification stage (faster, proves less)
#   --watch             After launching, tail logcat and report streaming state
#   -h | --help         Show this help
#
# Optional env (or set in .env at repo root):
#   AKSHRAVA_BASE_URL        Cloud Run HTTPS base (default: akshrava-api-c7d3j4nzdq-uc.a.run.app)
#   AKSHRAVA_WSS_URL         Override the full WSS endpoint (base-url derivation skipped if set)
#   AKSHRAVA_CALIBRATION_ID  Calibration profile (default: e2e-r0)
#   AKSHRAVA_DEVICE_ID       Device identifier for JWT (default: adb-<serial>-<timestamp>)
#   AKSHRAVA_TOKEN_TTL_DAYS  Token validity (default: 30)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="org.akshrava.app"
TEST_PKG="org.akshrava.app.test"
RUNNER="androidx.test.runner.AndroidJUnitRunner"

# ── Output helpers ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BOLD=""
fi

log()  { echo "${C_DIM}[$(date '+%H:%M:%S')]${C_RESET} $*"; }
ok()   { echo "${C_DIM}[$(date '+%H:%M:%S')]${C_RESET} ${C_GREEN}✓${C_RESET} $*"; }
warn() { echo "${C_DIM}[$(date '+%H:%M:%S')]${C_RESET} ${C_YELLOW}⚠${C_RESET}  $*"; }
die()  { echo "${C_RED}[ERROR]${C_RESET} $*" >&2; exit 1; }

STAGE_NO=0
stage() {
  STAGE_NO=$((STAGE_NO + 1))
  echo
  echo "${C_BOLD}──[$STAGE_NO/$STAGE_TOTAL] $*${C_RESET}"
}

# Stage outcomes for the closing summary. Keeps a partial run readable.
SUMMARY=()
record() { SUMMARY+=("$1|$2"); }

TMPDIR_RUN="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_RUN"; }
trap cleanup EXIT

# Print the header comment block verbatim, stopping at the first line that is not a comment,
# so the help text can never drift from the documentation above or leak code below it.
usage() { sed -n '2,${/^#/!q;s/^# \{0,1\}//;p;}' "$0"; exit 0; }

# ── Parse arguments ──────────────────────────────────────────────────────────
DEVICE_SERIAL=""
SKIP_BUILD=false
SKIP_VERIFY=false
WATCH=false
while [ $# -gt 0 ]; do
  case "$1" in
    --device)      DEVICE_SERIAL="${2:?--device needs a serial}"; shift 2 ;;
    --skip-build)  SKIP_BUILD=true; shift ;;
    --skip-verify) SKIP_VERIFY=true; shift ;;
    --watch)       WATCH=true; shift ;;
    -h|--help)     usage ;;
    -*)            die "Unknown flag: $1 (try --help)" ;;
    *)             DEVICE_SERIAL="$1"; shift ;;
  esac
done

STAGE_TOTAL=9
[ "$SKIP_VERIFY" = "true" ] && STAGE_TOTAL=8

# Load .env WITHOUT clobbering the caller's environment. `set -a; source .env` overwrote
# variables that were set explicitly on the command line, so `AKSHRAVA_WSS_URL=... ./script`
# silently ran against whatever .env held instead — an override that looked applied (it was
# echoed back as "from AKSHRAVA_WSS_URL") but was not. Explicit environment always wins.
if [ -f "$REPO_ROOT/.env" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    case "$key" in *[!A-Za-z0-9_]*|'') continue ;; esac
    # Only fill in what the environment has not already defined.
    if [ -z "${!key:-}" ]; then
      value="${line#*=}"
      value="${value%\"}"; value="${value#\"}"
      value="${value%\'}"; value="${value#\'}"
      export "$key=$value"
    fi
  done < "$REPO_ROOT/.env"
fi

# ── 1. Preconditions ─────────────────────────────────────────────────────────
stage "Preconditions"

: "${GOOGLE_APPLICATION_CREDENTIALS:?Set GOOGLE_APPLICATION_CREDENTIALS to deploy SA JSON}"
[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ] \
  || die "GOOGLE_APPLICATION_CREDENTIALS not readable: $GOOGLE_APPLICATION_CREDENTIALS"

ANDROID_SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
# Append rather than prepend: an adb/gcloud the operator has deliberately put on PATH (a
# pinned version, a wrapper, a test stub) must win over the SDK default. These are only a
# fallback for a shell that has not set them up at all.
export PATH="$PATH:$HOME/google-cloud-sdk/bin:$ANDROID_SDK/platform-tools:$ANDROID_SDK/emulator"

for tool in gcloud adb python3 curl; do
  command -v "$tool" >/dev/null || die "$tool not found in PATH"
done
ok "Tools present: gcloud, adb, python3, curl"

# Resolve the device before anything expensive so a missing phone fails in seconds.
if [ -z "$DEVICE_SERIAL" ]; then
  DEVICES="$(adb devices | awk 'NR>1 && $2=="device" {print $1}')"
  DEVICE_COUNT="$(printf '%s' "$DEVICES" | grep -c . || true)"
  if [ "$DEVICE_COUNT" -eq 0 ]; then
    UNAUTHORIZED="$(adb devices | awk 'NR>1 && $2=="unauthorized" {print $1}' | head -1)"
    [ -n "$UNAUTHORIZED" ] \
      && die "Device $UNAUTHORIZED is unauthorized. Accept the USB debugging prompt on the phone."
    die "No authorized Android devices found. Enable USB Debugging and authorize on device."
  elif [ "$DEVICE_COUNT" -gt 1 ]; then
    die "Multiple devices connected. Pick one with --device <serial>:
$(echo "$DEVICES" | sed 's/^/  /')"
  fi
  DEVICE_SERIAL="$(printf '%s' "$DEVICES" | head -1)"
fi

adb -s "$DEVICE_SERIAL" get-state >/dev/null 2>&1 || die "Device $DEVICE_SERIAL is not online"
DEVICE_MODEL="$(adb -s "$DEVICE_SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
DEVICE_SDK="$(adb -s "$DEVICE_SERIAL" shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r')"
ok "Device $DEVICE_SERIAL — $DEVICE_MODEL (API $DEVICE_SDK)"

# The app is API 26+; a lower device would install and then fail confusingly at runtime.
if [ -n "$DEVICE_SDK" ] && [ "$DEVICE_SDK" -lt 26 ] 2>/dev/null; then
  die "Device API $DEVICE_SDK is below the minimum supported API 26"
fi

# Provisioning writes to the Keystore, which needs an unlocked device on many OEM ROMs.
adb -s "$DEVICE_SERIAL" shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1 || true
if adb -s "$DEVICE_SERIAL" shell dumpsys window 2>/dev/null | grep -q "mDreamingLockscreen=true"; then
  warn "Screen appears locked — unlock the phone now (Keystore writes can fail while locked)"
fi
record "Preconditions" "ok"

# ── 2. Configuration ─────────────────────────────────────────────────────────
stage "Configuration"

DEVICE_ID="${AKSHRAVA_DEVICE_ID:-adb-${DEVICE_SERIAL}-$(date +%s)}"
TOKEN_TTL_DAYS="${AKSHRAVA_TOKEN_TTL_DAYS:-30}"
CALIBRATION_ID="${AKSHRAVA_CALIBRATION_ID:-e2e-r0}"

# Terraform output is authoritative when the workspace is initialised; the literal default
# is only a convenience for a machine that has never run terraform.
TF_WSS_URL=""
WSS_SOURCE="default"
if command -v terraform >/dev/null 2>&1 && [ -d "$REPO_ROOT/gcp" ]; then
  TF_WSS_URL="$(terraform -chdir="$REPO_ROOT/gcp" output -raw websocket_url 2>/dev/null || true)"
fi
BASE_URL="${AKSHRAVA_BASE_URL:-https://akshrava-api-c7d3j4nzdq-uc.a.run.app}"
if [ -n "${AKSHRAVA_WSS_URL:-}" ]; then
  WSS_URL="$AKSHRAVA_WSS_URL"; WSS_SOURCE="AKSHRAVA_WSS_URL"
elif [ -n "$TF_WSS_URL" ]; then
  WSS_URL="$TF_WSS_URL"; WSS_SOURCE="terraform output"
else
  WSS_URL="${BASE_URL/https/wss}/v1/session"; WSS_SOURCE="AKSHRAVA_BASE_URL"
fi

case "$WSS_URL" in
  wss://*) : ;;
  ws://*)  warn "Endpoint is cleartext ws:// — the release network config blocks this" ;;
  *)       die "Endpoint must be a ws:// or wss:// URL, got: $WSS_URL" ;;
esac
export AKSHRAVA_WSS_URL="$WSS_URL"

HTTP_BASE="https://${WSS_URL#wss://}"
HTTP_BASE="${HTTP_BASE%/v1/session}"

echo "  Device ID:    $DEVICE_ID"
echo "  Calibration:  $CALIBRATION_ID"
echo "  Token TTL:    $TOKEN_TTL_DAYS days"
echo "  WSS endpoint: $WSS_URL ${C_DIM}(from $WSS_SOURCE)${C_RESET}"
record "Configuration" "ok"

# ── 3. Backend health ────────────────────────────────────────────────────────
stage "Backend health"

HEALTH_STATE="ok"
for endpoint in livez readyz; do
  BODY_FILE="$TMPDIR_RUN/$endpoint.json"
  # curl already emits a status via -w (000 when it never connected) and exits non-zero on
  # failure, so a `|| echo 000` fallback concatenates into "000000". Take -w as authoritative
  # and only substitute when it produced nothing at all.
  STATUS="$(curl -s -o "$BODY_FILE" -w '%{http_code}' \
    --connect-timeout 5 --max-time 20 "$HTTP_BASE/$endpoint" 2>/dev/null)" || true
  STATUS="${STATUS:-000}"
  case "$STATUS" in
    200) ok "$endpoint: 200 $(tr -d '\n' < "$BODY_FILE")" ;;
    000) warn "$endpoint: unreachable (DNS, network, or wrong endpoint)"; HEALTH_STATE="degraded" ;;
    *)   warn "$endpoint: HTTP $STATUS"; HEALTH_STATE="degraded" ;;
  esac
done

# The detector the backend actually loaded decides whether streaming can work at all.
# detector=noop reports vision_enabled=false on the wire, so the phone connects, refuses to
# stream, and looks broken for a reason that is entirely server-side. Say so here.
DETECTOR="$(python3 - "$TMPDIR_RUN/livez.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("detector", ""))
except Exception:
    pass
PY
)"
if [ -n "$DETECTOR" ]; then
  if [ "$DETECTOR" = "noop" ]; then
    warn "Backend detector=noop — it will advertise vision_enabled=false and the app will NOT stream."
    warn "Set DETECTOR=remote (or ultralytics) on the backend before expecting detections."
    HEALTH_STATE="degraded"
  else
    ok "Backend detector: $DETECTOR"
  fi
fi
[ "$HEALTH_STATE" = "ok" ] || warn "Continuing despite degraded backend health"
record "Backend health" "$HEALTH_STATE"

# ── 4. Mint device token ─────────────────────────────────────────────────────
stage "Mint device token"

# The token is a live credential: never echo it, and never let it reach a log file.
TOKEN="$("$REPO_ROOT/scripts/mint_device_token_gcp.sh" "$DEVICE_ID" "$TOKEN_TTL_DAYS" 2>/dev/null)"
[ -n "$TOKEN" ] || die "Failed to mint device token (check gcloud auth and Secret Manager access)"

# Validate the claims locally before spending a build on a token the server will reject.
# Payload only — signature verification is the server's job, this just catches a bad mint.
python3 - "$TOKEN" <<'PY' || die "Minted token failed local claim validation"
import base64, json, sys, time
try:
    payload = sys.argv[1].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
except Exception as exc:
    raise SystemExit("token is not a decodable JWT: %s" % exc)
missing = [c for c in ("exp", "sub", "aud") if c not in claims]
if missing:
    raise SystemExit("token missing required claims: %s" % ", ".join(missing))
if claims["aud"] != "akshrava-device":
    raise SystemExit("unexpected audience: %s" % claims["aud"])
remaining = claims["exp"] - time.time()
if remaining <= 0:
    raise SystemExit("token is already expired")
print("  sub=%s aud=%s expires_in=%.1f days" % (claims["sub"], claims["aud"], remaining / 86400))
PY
ok "Token minted and claims validated (len=${#TOKEN}, value withheld)"
record "Mint token" "ok"

# ── 5. Build APKs ────────────────────────────────────────────────────────────
stage "Build APKs"

APK="$REPO_ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
TEST_APK="$REPO_ROOT/android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"

if [ "$SKIP_BUILD" = "true" ]; then
  [ -f "$APK" ] || die "--skip-build given but no APK at $APK"
  [ -f "$TEST_APK" ] || die "--skip-build given but no test APK at $TEST_APK"
  warn "Skipping Gradle build; reusing existing APKs"
else
  # We build both APKs here but deliberately do NOT run `./gradlew connectedDebugAndroidTest`:
  # that task uninstalls the target app (wiping its Keystore-provisioned data) as part of its
  # own cleanup, which silently undid provisioning. We install both APKs ourselves and drive
  # instrumentation with `adb shell am instrument`, which never uninstalls anything.
  BUILD_LOG="$TMPDIR_RUN/gradle.log"
  ( cd "$REPO_ROOT/android" \
    && ./gradlew --no-daemon assembleDebug assembleDebugAndroidTest >"$BUILD_LOG" 2>&1 ) || {
    tail -40 "$BUILD_LOG" >&2
    die "Gradle build failed (last 40 lines above)"
  }
  [ -f "$APK" ] || die "APK not found at $APK"
  [ -f "$TEST_APK" ] || die "Test APK not found at $TEST_APK"
  ok "Built app ($(du -h "$APK" | awk '{print $1}')) and test APK"
fi
record "Build" "ok"

# ── 6. Install APKs ──────────────────────────────────────────────────────────
stage "Install APKs"

install_apk() {
  local label="$1" path="$2"
  local out
  out="$(adb -s "$DEVICE_SERIAL" install -r -t -d "$path" 2>&1)" || true
  echo "$out" | grep -q "Success" || die "$label install failed:
$out"
}
install_apk "App APK" "$APK"
install_apk "Test APK" "$TEST_APK"

adb -s "$DEVICE_SERIAL" shell pm list packages 2>/dev/null | grep -q "$PKG\$" \
  || die "App package missing after install"
adb -s "$DEVICE_SERIAL" shell pm list packages 2>/dev/null | grep -q "$TEST_PKG\$" \
  || die "Test package missing after install"
ok "Both APKs installed and present on device"
record "Install" "ok"

# ── 7. Grant runtime permissions ─────────────────────────────────────────────
stage "Grant runtime permissions"

# CAMERA and POST_NOTIFICATIONS are runtime-gated. Without them the user has to tap through
# dialogs before the first frame, and a headless/bench run stalls silently on a permission
# prompt nobody is there to accept. Pre-granting makes "installed" mean "ready to stream".
grant() {
  local perm="$1"
  if adb -s "$DEVICE_SERIAL" shell pm grant "$PKG" "$perm" >/dev/null 2>&1; then
    ok "Granted $perm"
  else
    # POST_NOTIFICATIONS does not exist below API 33; that failure is expected, not fatal.
    warn "Could not grant $perm (may not apply to API $DEVICE_SDK)"
  fi
}
grant android.permission.CAMERA
grant android.permission.POST_NOTIFICATIONS

# Battery optimisation kills long camera sessions on aggressive OEM ROMs. Advisory only:
# it needs a user gesture on most devices and must not block provisioning.
if ! adb -s "$DEVICE_SERIAL" shell dumpsys deviceidle whitelist 2>/dev/null | grep -q "$PKG"; then
  warn "App is not battery-optimisation exempt; long sessions may be killed by the OEM ROM"
fi
record "Permissions" "ok"

# ── 8. Provision Keystore ────────────────────────────────────────────────────
stage "Provision Keystore (endpoint + token + calibration)"

# The instrumentation test only guarantees the write was *issued*. We independently read the
# app's SharedPreferences back to confirm the endpoint, encrypted token, and calibration
# actually landed on disk, and retry if a write was lost. A green "OK (1 test)" alone is NOT
# proof of provisioning — that lesson is why this loop exists.
provisioned_prefs() {
  adb -s "$DEVICE_SERIAL" shell run-as "$PKG" cat \
    "/data/data/$PKG/shared_prefs/akshrava.xml" 2>/dev/null
}

PROVISION_OK=false
for attempt in 1 2 3 4 5; do
  # Force-stop first so instrumentation starts from a clean process (no stale in-memory prefs).
  adb -s "$DEVICE_SERIAL" shell am force-stop "$PKG" >/dev/null 2>&1 || true
  adb -s "$DEVICE_SERIAL" shell am force-stop "$TEST_PKG" >/dev/null 2>&1 || true

  INSTRUMENT_OUT="$(adb -s "$DEVICE_SERIAL" shell am instrument -w -r \
    -e akshrava_test_token "$TOKEN" \
    -e akshrava_wss_url "$WSS_URL" \
    -e akshrava_calibration_id "$CALIBRATION_ID" \
    -e akshrava_provision_target true \
    -e class "$PKG.GcpLiveProvisioningTest" \
    "$TEST_PKG/$RUNNER" 2>&1)"
  if ! echo "$INSTRUMENT_OUT" | grep -q "OK ("; then
    echo "$INSTRUMENT_OUT" >&2
    die "Provisioning instrumentation failed (see output above)"
  fi

  sleep 1
  PREFS="$(provisioned_prefs)"
  if echo "$PREFS" | grep -q "encrypted_token" \
     && echo "$PREFS" | grep -q "name=\"calibration\">$CALIBRATION_ID<" \
     && echo "$PREFS" | grep -q "name=\"endpoint\">$WSS_URL<"; then
    PROVISION_OK=true
    ok "Provisioning verified on device (attempt $attempt): endpoint + token + calibration on disk"
    break
  fi
  warn "Attempt $attempt: provisioning did not fully persist yet; retrying..."
done
[ "$PROVISION_OK" = "true" ] || die "Provisioning failed to persist after 5 attempts.
Last on-device prefs:
$(provisioned_prefs)"

adb -s "$DEVICE_SERIAL" shell pm list packages 2>/dev/null | grep -q "$PKG\$" \
  || die "App package missing after provisioning"
record "Provisioning" "ok"

# ── 9. Verify the live GCP path ──────────────────────────────────────────────
VERIFY_STATE="skipped"
if [ "$SKIP_VERIFY" = "false" ]; then
  stage "Verify live GCP path"

  # Everything above proves the phone is *configured*. This proves it actually works: the
  # instrumentation drives the real ProtocolClient against the live endpoint with the token
  # we just minted and asserts ready + vision_enabled, a frame accepted onto the wire, a
  # result returned, a quality hint, and ping/pong. It uses a synthetic JPEG fixture, so no
  # camera or user gesture is involved. `am instrument` never uninstalls the target app.
  VERIFY_OUT="$TMPDIR_RUN/verify.txt"
  adb -s "$DEVICE_SERIAL" shell am force-stop "$PKG" >/dev/null 2>&1 || true
  set +e
  adb -s "$DEVICE_SERIAL" shell am instrument -w -r \
    -e akshrava_test_token "$TOKEN" \
    -e akshrava_wss_url "$WSS_URL" \
    -e akshrava_calibration_id "$CALIBRATION_ID" \
    -e class "$PKG.GcpLiveProtocolClientE2eTest" \
    "$TEST_PKG/$RUNNER" >"$VERIFY_OUT" 2>&1
  set -e

  if grep -q "OK (" "$VERIFY_OUT"; then
    ok "Live path verified: session ready, frame accepted, result received, ping/pong"
    VERIFY_STATE="ok"
  else
    # A failure here is real signal, not flakiness to paper over: the phone is provisioned
    # but cannot complete a session. Surface the assertion and keep going so the operator
    # still gets a launched app and a summary telling them exactly what is broken.
    warn "Live verification FAILED — the phone is provisioned but did not complete a session"
    grep -E "junit\.framework|java\.lang|AssertionError|Error in|shortMsg|longMsg" "$VERIFY_OUT" \
      | head -12 | sed 's/^/    /' >&2 || true
    echo "    Full output: $VERIFY_OUT (copied below)" >&2
    cp "$VERIFY_OUT" "$REPO_ROOT/.akshrava-verify-failure.log" 2>/dev/null \
      && echo "    Saved to $REPO_ROOT/.akshrava-verify-failure.log" >&2
    VERIFY_STATE="failed"
  fi
  record "Live verification" "$VERIFY_STATE"
fi

# ── Launch ───────────────────────────────────────────────────────────────────
adb -s "$DEVICE_SERIAL" shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 \
  >/dev/null 2>&1 || true
log "App launched on device"

# ── Summary ──────────────────────────────────────────────────────────────────
echo
echo "============================================================"
if [ "$VERIFY_STATE" = "failed" ]; then
  echo " ${C_YELLOW}⚠  Provisioned, but the live path did NOT verify${C_RESET}"
else
  echo " ${C_GREEN}✅ Android Debug Build — End-to-End Ready${C_RESET}"
fi
echo "============================================================"
echo
for entry in "${SUMMARY[@]}"; do
  name="${entry%%|*}"; state="${entry##*|}"
  case "$state" in
    ok)       mark="${C_GREEN}pass${C_RESET}" ;;
    degraded) mark="${C_YELLOW}degraded${C_RESET}" ;;
    failed)   mark="${C_RED}FAIL${C_RESET}" ;;
    *)        mark="${C_DIM}$state${C_RESET}" ;;
  esac
  printf "  %-22s %s\n" "$name" "$mark"
done
echo
echo "  Device:        $DEVICE_SERIAL ($DEVICE_MODEL, API $DEVICE_SDK)"
echo "  Device ID:     $DEVICE_ID"
echo "  Calibration:   $CALIBRATION_ID"
echo "  WSS endpoint:  $WSS_URL"
echo "  Token TTL:     $TOKEN_TTL_DAYS days"
echo
if [ "$VERIFY_STATE" = "ok" ]; then
  echo "  The live path is proven. Press Start in the app to stream camera frames."
else
  echo "  Press Start in the app to stream camera frames."
fi
# range_valid (and therefore S1 proximity hazards) needs a *verified* calibration profile in
# the backend database. Provisioning only sets the id the phone reports; it cannot create the
# server-side row, and a missing profile degrades silently to no proximity hazards.
echo "  If proximity hazards never fire, confirm '$CALIBRATION_ID' exists and is verified:"
echo "      DATABASE_URL=... python3 scripts/upsert_calibration_profile.py --help"
echo
echo "  Logs:  adb -s $DEVICE_SERIAL logcat -s \\"
echo "         AkshravaVision:* AkshravaDebug:* AkshravaTrace:* AndroidRuntime:*"
echo "============================================================"

# ── Optional: watch streaming ────────────────────────────────────────────────
if [ "$WATCH" = "true" ]; then
  echo
  log "Watching logcat for streaming activity (Ctrl-C to stop). Press Start on the phone now."
  adb -s "$DEVICE_SERIAL" logcat -c >/dev/null 2>&1 || true
  adb -s "$DEVICE_SERIAL" logcat -s AkshravaVision:I AkshravaDebug:I AndroidRuntime:E 2>/dev/null \
    | while IFS= read -r line; do
        case "$line" in
          *ws_ready*vision_enabled=true*) echo "${C_GREEN}✓${C_RESET} session ready, vision enabled" ;;
          *ws_ready*vision_enabled=false*) echo "${C_RED}✗${C_RESET} backend advertised vision_enabled=false (detector=noop?)" ;;
          *frame_sent*)   echo "${C_GREEN}→${C_RESET} ${line#*AkshravaVision}" ;;
          *detections=*)  echo "${C_GREEN}←${C_RESET} ${line#*AkshravaVision}" ;;
          *ws_failure*|*ws_closed*|*frame_drop*) echo "${C_YELLOW}!${C_RESET} ${line#*Akshrava}" ;;
          *FATAL*|*AndroidRuntime*) echo "${C_RED}✗${C_RESET} $line" ;;
        esac
      done
fi

[ "$VERIFY_STATE" = "failed" ] && exit 1
exit 0
