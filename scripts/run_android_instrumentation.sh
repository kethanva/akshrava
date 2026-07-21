#!/usr/bin/env bash
set -euo pipefail

APP_APK="${1:?usage: run_android_instrumentation.sh APP_APK TEST_APK RESULT_DIR}"
TEST_APK="${2:?usage: run_android_instrumentation.sh APP_APK TEST_APK RESULT_DIR}"
RESULT_DIR="${3:?usage: run_android_instrumentation.sh APP_APK TEST_APK RESULT_DIR}"
ADB_BIN="${ADB_BIN:-adb}"
RUNNER="org.akshrava.app.test/androidx.test.runner.AndroidJUnitRunner"

[[ -f "$APP_APK" ]] || { echo "Application APK not found: $APP_APK" >&2; exit 2; }
[[ -f "$TEST_APK" ]] || { echo "Test APK not found: $TEST_APK" >&2; exit 2; }
mkdir -p "$RESULT_DIR"
RESULT_FILE="$RESULT_DIR/instrumentation.txt"

# The emulator action has its own boot check, but ADB can briefly become unavailable
# immediately afterwards on hosted runners. Wait for both Android and PackageManager.
for attempt in {1..30}; do
  if [[ "$($ADB_BIN shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == "1" ]] && \
     $ADB_BIN shell pm path android >/dev/null 2>&1; then
    break
  fi
  if (( attempt == 30 )); then
    echo "Emulator lost ADB connectivity after boot" | tee "$RESULT_FILE" >&2
    exit 3
  fi
  $ADB_BIN reconnect >/dev/null 2>&1 || true
  sleep 2
done

$ADB_BIN install -r -t "$APP_APK"
$ADB_BIN install -r -t "$TEST_APK"

set +e
$ADB_BIN shell am instrument -w -r "$RUNNER" 2>&1 | tee "$RESULT_FILE"
instrument_status=${PIPESTATUS[0]}
set -e

if (( instrument_status != 0 )) || grep -Eq 'FAILURES!!!|INSTRUMENTATION_FAILED|Process crashed' "$RESULT_FILE"; then
  echo "Android instrumentation failed (status $instrument_status)" >&2
  exit 4
fi

grep -Eq '^OK \([0-9]+ tests?\)$' "$RESULT_FILE" || {
  echo "Android instrumentation did not report a successful test summary" >&2
  exit 5
}
