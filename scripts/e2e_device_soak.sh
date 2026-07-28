#!/usr/bin/env bash
# e2e_device_soak.sh — drive the REAL installed app on a REAL device for a sustained session
# and prove (or disprove) a continuous connection to the live backend.
#
# This is the physical-device complement to scripts/soak_session.py (which simulates the wire
# protocol without a phone). Use this one when the question is "does the app itself, running on
# real hardware with the real camera/TTS/OEM power management, stay connected" — the class of bug
# a protocol-level simulation cannot see (screen sleep killing CameraX, OEM TTS force-stop,
# wake-lock expiry, foreground-service death).
#
# Requires a provisioned device (endpoint + token + calibration already on disk). Run
# scripts/install_android_debug_full.sh first if the device has not been provisioned.
#
# Usage:
#   ./scripts/e2e_device_soak.sh [device_serial] [duration_seconds] [log_path]
#   ./scripts/e2e_device_soak.sh --help
#
# Env overrides:
#   ADB                 path to adb (default: first of $ANDROID_HOME/platform-tools/adb,
#                       ~/Library/Android/sdk/platform-tools/adb, or `adb` on PATH)
#   CHECKPOINT_SECS     how often to print a progress line (default: 30)
#   BACKEND_URL         HTTPS base to preflight (default: derived from repo .env AKSHRAVA_WSS_URL,
#                       wss://host/path -> https://host)
#   SKIP_HEALTH_CHECK   1 to skip the backend /livez + /readyz preflight
#   SKIP_PERMISSION_CHECK 1 to skip the CAMERA runtime-permission preflight
#   RESULT_JSON         path to also write a machine-readable JSON summary
#   LOGCAT_TAGS         space-separated tags to capture (default: the four below)
#
# What it does:
#   1. Preflights: adb device is authorized (and unambiguous), backend is reachable, CAMERA
#      permission is actually granted — all three are common reasons a soak silently proves
#      nothing rather than failing loudly.
#   2. Starts TWO logcat captures before launching the app: the tag-filtered app log (what you
#      read), and a broad crash channel (`AndroidRuntime:E *:F`) that the tag filter would
#      otherwise hide entirely — a Kotlin exception never carries the app's own log tags.
#   3. Launches MainActivity and taps Start via uiautomator (resource-id based, not hardcoded
#      coordinates), then confirms the tap actually registered by watching the log for
#      `svc_start` rather than assuming a tap succeeded.
#   4. Holds the session for the full duration, printing frames-sent / results-received / pid
#      every CHECKPOINT_SECS. Exits the wait loop immediately (not at the full duration) if the
#      process dies unexpectedly — a dead process for the remaining 9 minutes is not new
#      information worth waiting for.
#   5. Taps Stop, confirms teardown in the log (falls back to `am force-stop` if the tap did not
#      register, and reports that fallback), stops both logcat captures, and prints a pass/fail
#      verdict against the failure signatures documented in AGENT.md — including that frames
#      were actually sent (a socket can be perfectly healthy while camera permission silently
#      blocks every frame, which the original connection-only check could not see).
#
# Exit code is 0 only if: the app was reachable and provisioned, the connection never had to
# reconnect, no failure/crash signature appeared, the process never died unexpectedly, and a
# real majority of sent frames got an answer back.

set -uo pipefail

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,${/^#/!q;s/^# \{0,1\}//;p;}' "$0"
  exit 0
fi

SERIAL="${1:-}"
DURATION_S="${2:-660}"
LOG="${3:-.cursor/debug-$(date +%s).log}"
CRASH_LOG="${LOG%.log}.crashes.log"
CHECKPOINT_SECS="${CHECKPOINT_SECS:-30}"
LOGCAT_TAGS="${LOGCAT_TAGS:-AkshravaDebug AkshravaVision AkshravaConnection AkshravaTrace}"
PKG="org.akshrava.app"
MIN_RESULT_RATIO="0.5"   # below this, treat as "connected but not actually streaming"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Output helpers (match install_android_debug_full.sh) ───────────────────
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
else
  C_RESET=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""
fi
say()  { echo "${C_DIM}[$(date '+%H:%M:%S')]${C_RESET} $*"; }
ok()   { echo "${C_DIM}[$(date '+%H:%M:%S')]${C_RESET} ${C_GREEN}✓${C_RESET} $*"; }
warn() { echo "${C_DIM}[$(date '+%H:%M:%S')]${C_RESET} ${C_YELLOW}⚠${C_RESET}  $*"; }
die()  { echo "${C_RED}[FATAL]${C_RESET} $*" >&2; exit 2; }

# `grep -c` prints "0" on zero matches but still exits 1 (no match found). A naive
# `$(grep -c ... || echo 0)` fallback then runs BOTH branches of the `||` into the same command
# substitution — grep's own "0" plus the fallback's "0" — silently doubling every zero-count
# metric into two stacked "0" lines. Only a missing FILE should fall back to 0 (grep -c prints
# nothing and exits 2 in that case); a genuine zero-match count must be trusted as-is.
count_matches() {
  local n
  n=$(grep -cE "$1" "$2" 2>/dev/null)
  echo "${n:-0}"
}

# ── Resolve adb ──────────────────────────────────────────────────────────────
if [ -z "${ADB:-}" ]; then
  for candidate in "${ANDROID_HOME:-}/platform-tools/adb" "$HOME/Library/Android/sdk/platform-tools/adb" "$(command -v adb 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then ADB="$candidate"; break; fi
  done
fi
[ -z "${ADB:-}" ] && die "adb not found. Set ADB=/path/to/adb"

# ── Resolve and validate the device ─────────────────────────────────────────
DEVICE_LINES="$("$ADB" devices | tail -n +2 | sed '/^$/d')"
if [ -z "$SERIAL" ]; then
  READY_COUNT=$(echo "$DEVICE_LINES" | awk '$2=="device"' | wc -l | tr -d ' ')
  if [ "$READY_COUNT" -eq 0 ]; then
    die "no connected device. 'adb devices' shows:\n$DEVICE_LINES"
  elif [ "$READY_COUNT" -gt 1 ]; then
    die "multiple devices connected; pass one explicitly:\n$(echo "$DEVICE_LINES" | awk '$2=="device"{print "  "$1}')"
  fi
  SERIAL="$(echo "$DEVICE_LINES" | awk '$2=="device"{print $1}')"
fi
STATE=$(echo "$DEVICE_LINES" | awk -v s="$SERIAL" '$1==s{print $2}')
case "$STATE" in
  device) : ;;
  unauthorized) die "device $SERIAL is unauthorized — accept the RSA key prompt on the phone" ;;
  offline)      die "device $SERIAL is offline — replug or 'adb kill-server'" ;;
  "")           die "device $SERIAL not found in 'adb devices'" ;;
  *)            die "device $SERIAL is in unexpected state '$STATE'" ;;
esac
A="$ADB -s $SERIAL"

mkdir -p "$(dirname "$LOG")"

# ── Preflight: backend reachability ─────────────────────────────────────────
if [ "${SKIP_HEALTH_CHECK:-0}" != "1" ]; then
  if [ -z "${BACKEND_URL:-}" ] && [ -f .env ]; then
    # Same derivation AppConfig's build-time WSS resolution uses: wss://host/path -> https://host.
    wss="$(grep -E '^AKSHRAVA_WSS_URL=' .env | head -1 | cut -d= -f2- | tr -d '"'"'"'')"
    [ -n "$wss" ] && BACKEND_URL="https://$(echo "$wss" | sed -E 's#^wss?://##; s#/.*##')"
  fi
  if [ -z "${BACKEND_URL:-}" ]; then
    warn "BACKEND_URL not set and could not derive one from .env; skipping health preflight"
  else
    say "backend preflight: $BACKEND_URL"
    live_code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$BACKEND_URL/livez" || echo "000")
    ready_code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$BACKEND_URL/readyz" || echo "000")
    [ "$live_code" = "200" ] || die "backend /livez returned $live_code — soaking a dead backend proves nothing"
    [ "$ready_code" = "200" ] || die "backend /readyz returned $ready_code — dependencies unavailable"
    ok "backend live and ready"
  fi
else
  warn "SKIP_HEALTH_CHECK=1 — not verifying the backend before soaking"
fi

# ── Preflight: app installed + camera permission actually granted ──────────
$A shell pm path "$PKG" >/dev/null 2>&1 || die "$PKG is not installed on $SERIAL — run install_android_debug_full.sh first"
VERSION_NAME=$($A shell dumpsys package "$PKG" | grep -m1 versionName | cut -d= -f2 | tr -d '\r ')
say "app installed: versionName=$VERSION_NAME"

if [ "${SKIP_PERMISSION_CHECK:-0}" != "1" ]; then
  cam_state=$($A shell dumpsys package "$PKG" | grep -m1 "android.permission.CAMERA:")
  if ! echo "$cam_state" | grep -q "granted=true"; then
    die "CAMERA permission is not granted on $SERIAL — every frame would be silently unsent. Fix: adb -s $SERIAL shell pm grant $PKG android.permission.CAMERA"
  fi
  ok "CAMERA permission granted"
else
  warn "SKIP_PERMISSION_CHECK=1 — not verifying CAMERA permission"
fi

# ── Find a button by resource id in the current UI hierarchy and tap its centre ─────────────
# uiautomator is the only reliable way to locate the right spot across screen sizes/densities;
# hardcoded coordinates silently miss on any device but the one they were recorded on. Retried
# because the dump occasionally returns truncated/empty output on a busy device.
tap_by_id() {
  local id="$1" attempt dump bounds x1 y1 x2 y2
  for attempt in 1 2 3; do
    $A shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
    dump="$($A shell cat /sdcard/ui.xml 2>/dev/null)"
    bounds="$(printf '%s' "$dump" \
      | tr '>' '\n' \
      | grep "resource-id=\"$PKG:id/$id\"" \
      | grep -o 'bounds="\[[0-9]*,[0-9]*\]\[[0-9]*,[0-9]*\]"' \
      | head -1 \
      | grep -o '[0-9]*')"
    [ -n "$bounds" ] && break
    sleep 1
  done
  [ -z "$bounds" ] && return 1
  x1=$(echo "$bounds" | sed -n 1p); y1=$(echo "$bounds" | sed -n 2p)
  x2=$(echo "$bounds" | sed -n 3p); y2=$(echo "$bounds" | sed -n 4p)
  local cx=$(( (x1 + x2) / 2 )) cy=$(( (y1 + y2) / 2 ))
  say "tapping $id at ($cx,$cy)"
  $A shell input tap "$cx" "$cy"
}

# Wait for a marker line to appear in $LOG, up to $1 seconds. Used instead of trusting a tap
# blindly succeeded (locked screen, missed hitbox, and a stale UI dump all fail silently).
wait_for_log_marker() {
  local marker="$1" timeout="$2" waited=0
  while [ "$waited" -lt "$timeout" ]; do
    grep -q "$marker" "$LOG" 2>/dev/null && return 0
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

say "device: $($A shell getprop ro.product.model | tr -d '\r') android $($A shell getprop ro.build.version.release | tr -d '\r') (api $($A shell getprop ro.build.version.sdk | tr -d '\r'))"
$A shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1
$A shell svc power stayon usb >/dev/null 2>&1
$A shell am force-stop "$PKG" >/dev/null 2>&1

# Best-effort lock-screen detection. A locked screen makes every subsequent tap land on the
# lockscreen, not the app, and the loop below would then "pass" on a session that never started —
# indistinguishable from a healthy run until you notice frames_sent stayed at 0.
lock_state=$($A shell dumpsys window 2>/dev/null | grep -m1 -oE 'mDreamingLockscreen=(true|false)' | cut -d= -f2)
if [ "$lock_state" = "true" ]; then
  warn "screen appears locked; attempting a wake + swipe-up unlock (no PIN entry possible)"
  $A shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1
  $A shell input swipe 540 1500 540 400 >/dev/null 2>&1
  sleep 1
  lock_state=$($A shell dumpsys window 2>/dev/null | grep -m1 -oE 'mDreamingLockscreen=(true|false)' | cut -d= -f2)
  [ "$lock_state" = "true" ] && die "device is locked behind a PIN/pattern — unlock it manually before running this script"
fi

# ── Start both logcat captures BEFORE launching, so nothing is missed ──────
$A logcat -c
: > "$LOG"
: > "$CRASH_LOG"
# shellcheck disable=SC2086
$A logcat -s $(printf '%s:V ' $LOGCAT_TAGS) > "$LOG" 2>&1 &
LOGCAT_PID=$!
# Custom app tags never carry a JVM crash or ANR; without this channel a real crash is
# indistinguishable in the main log from "the process quietly stopped".
$A logcat AndroidRuntime:E ActivityManager:W "*:S" > "$CRASH_LOG" 2>&1 &
CRASHLOG_PID=$!
say "logcat capturing to $LOG (pid $LOGCAT_PID), crash channel to $CRASH_LOG (pid $CRASHLOG_PID)"

STOPPED_CLEANLY=0
STOP_HANDLED=0
FINISHED=0
finish() {
  local exit_code=$?
  # Registered for both EXIT and INT/TERM: calling `exit` below re-fires the EXIT trap, which
  # would otherwise re-enter this function (double tap_by_id, double warning line). Idempotent.
  [ "$FINISHED" = "1" ] && exit "$exit_code"
  FINISHED=1
  # A Ctrl-C (or any error after this point) must not leave the assistance session running
  # unattended on the phone: AssistService is a foreground service that holds the camera, a live
  # WebSocket, and a wake lock, and none of that tears itself down just because this script's
  # process exited. Only skip this when Stop was already handled on the normal path below, or
  # when the app was never actually started (an early preflight die() before Start was tapped).
  if [ "$STOP_HANDLED" = "0" ] && [ "${APP_STARTED:-0}" = "1" ]; then
    warn "exiting before Stop was pressed — force-stopping the app on-device so it does not keep streaming unattended"
    tap_by_id btnStop >/dev/null 2>&1
    sleep 2
    $A shell am force-stop "$PKG" >/dev/null 2>&1
  fi
  kill "$LOGCAT_PID" "$CRASHLOG_PID" 2>/dev/null
  wait "$LOGCAT_PID" "$CRASHLOG_PID" 2>/dev/null
  $A shell svc power stayon false >/dev/null 2>&1
  exit "$exit_code"
}
trap finish EXIT INT TERM

$A shell am start -n "$PKG/.MainActivity" >/dev/null 2>&1
sleep 4
tap_by_id btnStart || die "could not locate btnStart (is the app provisioned? run install_android_debug_full.sh)"
APP_STARTED=1
if ! wait_for_log_marker "svc_start" 8; then
  die "tapped Start but no svc_start appeared in the log within 8s — the tap likely missed (locked screen? stale UI?)"
fi
ok "Start registered"

DIED_UNEXPECTEDLY=0
START_EPOCH=$(date +%s)
say "soaking for ${DURATION_S}s"
while [ $(( $(date +%s) - START_EPOCH )) -lt "$DURATION_S" ]; do
  sleep "$CHECKPOINT_SECS"
  elapsed=$(( $(date +%s) - START_EPOCH ))
  frames=$(count_matches "frame_sent id=" "$LOG")
  results=$(count_matches "AkshravaVision: frame=" "$LOG")
  alive=$($A shell pidof "$PKG" | tr -d '\r')
  say "t=${elapsed}s frames_sent=$frames results=$results pid=${alive:-DEAD}"
  $A shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1
  if [ -z "$alive" ]; then
    warn "process died mid-soak at t=${elapsed}s — ending early, this is itself a failure"
    DIED_UNEXPECTEDLY=1
    break
  fi
done

if [ "$DIED_UNEXPECTEDLY" = "0" ]; then
  say "pressing Stop"
  if tap_by_id btnStop && wait_for_log_marker "onDestroy\|event=client_close" 8; then
    STOPPED_CLEANLY=1
    ok "Stop registered cleanly"
  else
    warn "Stop tap did not register in the log within 8s; forcing teardown with am force-stop"
    $A shell am force-stop "$PKG" >/dev/null 2>&1
  fi
else
  # Already gone; nothing left to stop, and tapping a dead app's UI would be meaningless.
  $A shell am force-stop "$PKG" >/dev/null 2>&1
fi
STOP_HANDLED=1
sleep 3

# ── Verdict ──────────────────────────────────────────────────────────────────
# These are the exact signatures documented in AGENT.md as "the connection failed", not routine
# steady-state frame shedding (which logs constantly and is not a fault — see AGENT.md).
FAILURES=$(count_matches \
  "ws_drop|transport_drop|transport_failure|reconnect_scheduled|reconnect_executing|frame_slot_wedged|Assistance stalled|vision_unavailable|Connection restored|ws_hard_error" \
  "$LOG")
CRASHES=$(count_matches "FATAL EXCEPTION|ANR in $PKG|Process $PKG.*died" "$CRASH_LOG")
CONNECT_ATTEMPTS=$(count_matches "event=connect_attempt" "$LOG")
CONNECTED_MS=$(grep -oE "connectedForMs=[0-9]+" "$LOG" | tail -1 | cut -d= -f2)
FRAMES_SENT=$(count_matches "frame_sent id=" "$LOG")
RESULTS_RECEIVED=$(count_matches "AkshravaVision: frame=" "$LOG")

PASS=1
REASONS=()
[ "$CONNECT_ATTEMPTS" = "1" ]      || { PASS=0; REASONS+=("connect_attempts=$CONNECT_ATTEMPTS (expected exactly 1 — the socket reconnected)"); }
[ "$FAILURES" = "0" ]              || { PASS=0; REASONS+=("$FAILURES drop/reconnect signature(s) in $LOG"); }
[ "$CRASHES" = "0" ]               || { PASS=0; REASONS+=("$CRASHES crash/ANR signature(s) in $CRASH_LOG"); }
[ "$DIED_UNEXPECTEDLY" = "0" ]     || { PASS=0; REASONS+=("process died before Stop was pressed"); }
[ "$FRAMES_SENT" -gt 0 ]           || { PASS=0; REASONS+=("0 frames were ever sent — connected but not actually streaming"); }
if [ "$FRAMES_SENT" -gt 0 ]; then
  ratio_ok=$(awk -v r="$RESULTS_RECEIVED" -v f="$FRAMES_SENT" -v m="$MIN_RESULT_RATIO" 'BEGIN{print (r/f>=m)?1:0}')
  [ "$ratio_ok" = "1" ] || { PASS=0; REASONS+=("only $RESULTS_RECEIVED/$FRAMES_SENT frames got a result (< ${MIN_RESULT_RATIO} ratio)"); }
fi

echo
echo "======================== SOAK RESULT ========================"
echo "device:             $SERIAL ($VERSION_NAME)"
echo "log:                $LOG"
echo "crash log:          $CRASH_LOG"
echo "connect attempts:   $CONNECT_ATTEMPTS (1 = never reconnected)"
echo "failure signatures: $FAILURES  crash signatures: $CRASHES"
echo "frames sent:        $FRAMES_SENT   results received: $RESULTS_RECEIVED"
echo "connected for:      ${CONNECTED_MS:-unknown} ms"
echo "stopped cleanly:    $([ "$STOPPED_CLEANLY" = "1" ] && echo yes || echo no)"

if [ -n "${RESULT_JSON:-}" ]; then
  {
    echo "{"
    echo "  \"device\": \"$SERIAL\", \"version_name\": \"$VERSION_NAME\","
    echo "  \"connect_attempts\": $CONNECT_ATTEMPTS, \"failure_signatures\": $FAILURES, \"crash_signatures\": $CRASHES,"
    echo "  \"frames_sent\": $FRAMES_SENT, \"results_received\": $RESULTS_RECEIVED,"
    echo "  \"connected_ms\": ${CONNECTED_MS:-null}, \"stopped_cleanly\": $([ "$STOPPED_CLEANLY" = "1" ] && echo true || echo false),"
    echo "  \"verdict\": \"$([ "$PASS" = "1" ] && echo PASS || echo FAIL)\""
    echo "}"
  } > "$RESULT_JSON"
  say "wrote $RESULT_JSON"
fi

if [ "$PASS" = "1" ]; then
  echo "VERDICT: PASS — single continuous connection, real throughput, no drop/crash signatures"
  exit 0
else
  echo "VERDICT: FAIL —"
  for reason in "${REASONS[@]}"; do echo "  - $reason"; done
  exit 1
fi
