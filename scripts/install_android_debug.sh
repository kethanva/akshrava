#!/usr/bin/env bash
# install_android_debug.sh — Build the Akshrava debug APK and install it on a
# USB-connected Android phone.
#
# Usage:
#   ./scripts/install_android_debug.sh           # auto-detects device
#   ./scripts/install_android_debug.sh <serial>  # target a specific device serial
#
# Pre-requisites (checked automatically below):
#   1. Android SDK with platform-tools installed (ADB)
#   2. Java 17+
#   3. Phone has USB Debugging enabled (Settings → Developer Options → USB Debugging)
#   4. You have authorised the Mac in the "Allow USB debugging?" dialog on the phone
#
# Optional env (or set in .env at repo root):
#   AKSHRAVA_BASE_URL   Cloud Run HTTPS base URL (default: akshrava-api-c7d3j4nzdq-uc.a.run.app)
#   AKSHRAVA_WSS_URL    Full WSS endpoint; overrides AKSHRAVA_BASE_URL derivation
#
# Troubleshooting tips are printed if anything fails.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$REPO_ROOT/.env" ]; then set -a; source "$REPO_ROOT/.env"; set +a; fi
ANDROID_DIR="$REPO_ROOT/android"
APK_PATH="$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"

# ── 0. Resolve the live GCP WSS endpoint ──────────────────────────────────────
# Prefer terraform output (authoritative for whichever project/region is applied);
# fall back to the known pilot Cloud Run URL used by the other e2e scripts; allow
# explicit override via AKSHRAVA_BASE_URL / AKSHRAVA_WSS_URL / .env.
TF_WSS_URL=""
if command -v terraform &>/dev/null && [ -d "$REPO_ROOT/gcp" ]; then
    TF_WSS_URL=$(terraform -chdir="$REPO_ROOT/gcp" output -raw websocket_url 2>/dev/null || true)
fi
BASE_URL="${AKSHRAVA_BASE_URL:-https://akshrava-api-c7d3j4nzdq-uc.a.run.app}"
WSS_URL="${AKSHRAVA_WSS_URL:-${TF_WSS_URL:-${BASE_URL/https/wss}/v1/session}}"
export AKSHRAVA_WSS_URL="$WSS_URL"
echo "==> GCP WSS endpoint: $WSS_URL"

# ── 1. Find ADB ──────────────────────────────────────────────────────────────
ADB=""
for candidate in \
    "${ANDROID_HOME:-}/platform-tools/adb" \
    "/Users/admin/Library/Android/sdk/platform-tools/adb" \
    "$(command -v adb 2>/dev/null || true)" \
    "/usr/local/bin/adb" \
    "/opt/homebrew/bin/adb"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        ADB="$candidate"
        break
    fi
done

echo "=============================================="
echo "   Akshrava Debug APK — Build & Install"
echo "=============================================="
echo ""

if [ -z "$ADB" ]; then
    echo "❌  ADB not found."
    echo "    Install Android SDK Platform Tools:"
    echo "    https://developer.android.com/tools/releases/platform-tools"
    echo "    Or set ANDROID_HOME to your SDK directory."
    exit 1
fi
echo "✅  ADB found: $ADB"

# ── 2. Start ADB server ───────────────────────────────────────────────────────
"$ADB" start-server &>/dev/null || true

# ── 3. Check device(s) ────────────────────────────────────────────────────────
echo ""
echo "==> Scanning for connected Android devices..."
DEVICE_SERIAL="${1:-}"

RAW_DEVICES=$("$ADB" devices 2>&1)
echo "$RAW_DEVICES"

DEVICES=$(echo "$RAW_DEVICES" | awk 'NR>1 && $2=="device" {print $1}')
UNAUTH=$(echo "$RAW_DEVICES" | awk 'NR>1 && $2=="unauthorized" {print $1}')
OFFLINE=$(echo "$RAW_DEVICES" | awk 'NR>1 && $2=="offline" {print $1}')

if [ -n "$UNAUTH" ]; then
    echo ""
    echo "⚠️   Unauthorised device(s): $UNAUTH"
    echo ""
    echo "    ─── How to fix ───────────────────────────────────────────────"
    echo "    1. Unlock your phone screen"
    echo "    2. Look for the 'Allow USB debugging?' popup — tap ALLOW"
    echo "    3. Tick 'Always allow from this computer'"
    echo "    4. Re-run this script"
    echo "    ──────────────────────────────────────────────────────────────"
    echo ""
fi

if [ -n "$OFFLINE" ]; then
    echo "⚠️   Offline device(s): $OFFLINE"
    echo "    Try: $ADB kill-server && $ADB start-server"
    echo ""
fi

if [ -z "$DEVICES" ]; then
    echo ""
    echo "❌  No authorised Android devices found."
    echo ""
    echo "    ─── Checklist ────────────────────────────────────────────────"
    echo "    1. Enable Developer Options:"
    echo "       Settings → About Phone → tap 'Build Number' 7 times"
    echo ""
    echo "    2. Enable USB Debugging:"
    echo "       Settings → Developer Options → USB Debugging → ON"
    echo ""
    echo "    3. For Xiaomi / Realme / Vivo / Oppo / OnePlus, also enable:"
    echo "       Settings → Developer Options → USB Debugging (Security Settings) → ON"
    echo "       (Some OEMs require this extra toggle)"
    echo ""
    echo "    4. Use a DATA cable (not a charge-only cable)"
    echo "       Tip: try a different cable/port if the device still doesn't appear"
    echo ""
    echo "    5. Change USB mode on phone:"
    echo "       Pull down notification shade → tap 'Charging via USB' → select 'File Transfer'"
    echo "       (This sometimes triggers ADB re-detection)"
    echo ""
    echo "    6. Tap 'Allow' on the 'Allow USB debugging?' dialog on the phone"
    echo ""
    echo "    7. Run: $ADB kill-server && $ADB start-server && $ADB devices"
    echo ""
    echo "    ─── OEM-specific ─────────────────────────────────────────────"
    echo "    Xiaomi:  Also enable 'Install via USB' in Developer Options"
    echo "    Huawei:  Enable 'Allow ADB debugging in charge only mode' in Dev Options"
    echo "    Samsung: Accept the RSA fingerprint dialog that appears after pairing"
    echo "    ──────────────────────────────────────────────────────────────"
    exit 1
fi

DEVICE_COUNT=$(echo "$DEVICES" | grep -c . || true)
if [ -z "$DEVICE_SERIAL" ]; then
    if [ "$DEVICE_COUNT" -gt 1 ]; then
        echo "Multiple devices found — please pass a serial number:"
        echo "$DEVICES" | while read -r s; do
            MODEL=$("$ADB" -s "$s" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
            echo "    $s   ($MODEL)"
        done
        echo ""
        echo "Usage: ./scripts/install_android_debug.sh <serial>"
        exit 1
    fi
    DEVICE_SERIAL=$(echo "$DEVICES" | head -1)
fi

MODEL=$("$ADB" -s "$DEVICE_SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
ANDROID_VER=$("$ADB" -s "$DEVICE_SERIAL" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r')
SDK_VER=$("$ADB" -s "$DEVICE_SERIAL" shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r')
echo ""
echo "✅  Target device: $MODEL (Android $ANDROID_VER / SDK $SDK_VER) [$DEVICE_SERIAL]"

# ── 4. SDK version gate ───────────────────────────────────────────────────────
if [ -n "$SDK_VER" ] && [ "$SDK_VER" -lt 26 ]; then
    echo "❌  This app requires Android 8 (API 26). The phone is running SDK $SDK_VER."
    echo "    Please use a newer device."
    exit 1
fi

# ── 5. Build APK ─────────────────────────────────────────────────────────────
echo ""
echo "==> Building debug APK (this takes ~30s on first run)..."
cd "$ANDROID_DIR"
./gradlew assembleDebug --no-daemon 2>&1 | grep -E "^(BUILD|>|ERROR|FAILURE|WARNING)" || true
if [ ! -f "$APK_PATH" ]; then
    echo "❌  Build failed — APK not found at $APK_PATH"
    echo "    Run manually: cd android && ./gradlew assembleDebug"
    exit 1
fi
echo "✅  Build succeeded: $(du -sh "$APK_PATH" | awk '{print $1}') — $APK_PATH"

# ── 6. Install APK ────────────────────────────────────────────────────────────
echo ""
echo "==> Installing on $MODEL..."

INSTALL_FLAGS="-r"
# -t allows test APKs (debug builds) on stricter Android 10+ OEMs
# -d allows version downgrade which can happen when iterating debug builds
INSTALL_FLAGS="$INSTALL_FLAGS -t -d"

INSTALL_OUT=$("$ADB" -s "$DEVICE_SERIAL" install $INSTALL_FLAGS "$APK_PATH" 2>&1)
INSTALL_EXIT=$?

echo "$INSTALL_OUT"

if echo "$INSTALL_OUT" | grep -q "^Success"; then
    echo ""
    echo "✅  Installed successfully!"
elif echo "$INSTALL_OUT" | grep -q "INSTALL_FAILED_USER_RESTRICTED"; then
    echo ""
    echo "❌  INSTALL_FAILED_USER_RESTRICTED"
    echo "    The device blocks sideloading (installing APKs outside the Play Store)."
    echo ""
    echo "    Fix:"
    echo "    • Settings → Security → Install unknown apps → select ADB → Allow"
    echo "    • OR: Settings → Apps → Special app access → Install unknown apps"
    echo "    • Xiaomi: Settings → Developer Options → Install via USB → ON"
    echo ""
    exit 1
elif echo "$INSTALL_OUT" | grep -q "INSTALL_FAILED_UPDATE_INCOMPATIBLE"; then
    echo ""
    echo "⚠️   A different-signature build is already installed. Removing old version..."
    "$ADB" -s "$DEVICE_SERIAL" uninstall org.akshrava.app 2>&1 || true
    "$ADB" -s "$DEVICE_SERIAL" install $INSTALL_FLAGS "$APK_PATH"
    echo "✅  Installed after removing old version."
elif echo "$INSTALL_OUT" | grep -q "INSTALL_FAILED_OLDER_SDK"; then
    echo "❌  INSTALL_FAILED_OLDER_SDK — phone SDK is too old (need API 26+)."
    exit 1
elif echo "$INSTALL_OUT" | grep -q "INSTALL_FAILED_ALREADY_EXISTS"; then
    echo "✅  Already installed (same version). Forcing reinstall..."
    "$ADB" -s "$DEVICE_SERIAL" install -r -t -d "$APK_PATH"
elif [ $INSTALL_EXIT -ne 0 ]; then
    echo ""
    echo "❌  Install failed. Common causes:"
    echo "  • Charge-only USB cable (switch to a data cable)"
    echo "  • USB Debugging not enabled"
    echo "  • Pending 'Allow USB debugging?' dialog on phone"
    echo "  • Xiaomi: Enable 'Install via USB' in Developer Options"
    exit 1
fi

# ── 7. Configure Port Forwarding (Local Dev) ──────────────────────────────────
echo ""
echo "==> Configuring ADB reverse port forwarding (tcp:8000 -> tcp:8000)..."
"$ADB" -s "$DEVICE_SERIAL" reverse tcp:8000 tcp:8000 || echo "⚠️   Failed to reverse port 8000"

# ── 8. Launch app ─────────────────────────────────────────────────────────────
echo ""
echo "==> Launching Akshrava on device..."
"$ADB" -s "$DEVICE_SERIAL" shell am start -n org.akshrava.app/.MainActivity 2>&1 || true

echo ""
echo "=============================================="
echo "   ✅  Akshrava is installed and launched!"
echo "=============================================="
echo ""
echo "The WSS endpoint below is already baked into this debug build (BuildConfig.DEFAULT_WSS_ENDPOINT)."
echo "Only re-enter it in the app if you previously provisioned a different endpoint on this phone:"
echo "  WSS endpoint:  $WSS_URL"
echo "  Device token:  Run: GOOGLE_APPLICATION_CREDENTIALS=<your-creds.json> ./scripts/print_android_pilot_provisioning.sh"
echo "  Calibration:   e.g. 'volunteer-test-1'"
echo ""
echo "To see live logs:"
echo "  $ADB -s $DEVICE_SERIAL logcat -s AssistService:* MainActivity:* AndroidRuntime:* 2>&1"
echo ""
