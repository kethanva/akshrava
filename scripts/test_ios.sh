#!/usr/bin/env bash
# scripts/test_ios.sh — Akshrava iOS Test Suite (fail-closed)
#
# Mirrors Android ./gradlew :app:testDebugUnitTest.
# Build/test failures exit non-zero. Do not treat toolchain noise as success.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IOS_DIR="$REPO_ROOT/ios/Akshrava"

echo "=== Akshrava iOS Test Suite ==="
echo "  iOS directory: $IOS_DIR"

if [[ ! -d "$IOS_DIR" ]]; then
  echo "ERROR: missing iOS package at $IOS_DIR"
  exit 1
fi

# Prefer a full Xcode toolchain. Command Line Tools alone has hung here on emit-module.
if [[ -z "${DEVELOPER_DIR:-}" ]]; then
  if [[ -d /Applications/Xcode.app/Contents/Developer ]]; then
    export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  elif [[ -d /Applications/Xcode_16.4.app/Contents/Developer ]]; then
    export DEVELOPER_DIR=/Applications/Xcode_16.4.app/Contents/Developer
  fi
fi
echo "  DEVELOPER_DIR=${DEVELOPER_DIR:-$(xcode-select -p 2>/dev/null || echo unknown)}"

echo ""
echo "--- Step 1: Release version parity ---"
EXPECTED_VERSION="$(
  python3 -c "
import re
from pathlib import Path
text = (Path(r'$REPO_ROOT') / 'backend/pyproject.toml').read_text()
print(re.search(r'^version\\s*=\\s*\"([^\"]+)\"', text, re.M).group(1))
"
)"
echo "  expected version: $EXPECTED_VERSION"
python3 "$REPO_ROOT/scripts/check_release_version.py" "v${EXPECTED_VERSION}"

if [[ "${SKIP_SWIFT:-0}" == "1" ]]; then
  echo "SKIP_SWIFT=1 — skipping swift build/test (explicit only)"
else
  if ! command -v swift >/dev/null 2>&1; then
    echo "ERROR: swift not found on PATH"
    exit 1
  fi

  XCODE_PATH="$(xcode-select -p 2>/dev/null || true)"
  if [[ "$XCODE_PATH" == *CommandLineTools* && -z "${ALLOW_CLT_SWIFT:-}" ]]; then
    echo "ERROR: Active developer dir is Command Line Tools ($XCODE_PATH)."
    echo "Install/select full Xcode (xcode-select -s /Applications/Xcode.app/Contents/Developer)"
    echo "or set ALLOW_CLT_SWIFT=1 to override (not recommended; CLT has hung on this package)."
    exit 1
  fi

  if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
    echo ""
    echo "--- Step 2: swift build ---"
    (cd "$IOS_DIR" && swift build)
    echo "swift build: PASSED"
  fi

  echo ""
  echo "--- Step 3: swift test ---"
  (cd "$IOS_DIR" && swift test)
  echo "swift test: PASSED"
fi

echo ""
echo "--- Step 4: Safety boundary audit ---"
FORBIDDEN_TERMS=(
  "crossing decision"
  "collision avoidance"
  "clear path"
  "safe to cross"
  "you can go"
  "proceed safely"
  "approach speed"
)

VIOLATIONS=0
for TERM in "${FORBIDDEN_TERMS[@]}"; do
  MATCHES="$(grep -rn -i "$TERM" "$IOS_DIR/Akshrava" \
    --include="*.swift" \
    --exclude-dir=".build" 2>/dev/null | grep -v '^\s*//' | grep -v '///' || true)"
  if [[ -n "$MATCHES" ]]; then
    echo "SAFETY VIOLATION: '$TERM' found:"
    echo "$MATCHES"
    VIOLATIONS=$((VIOLATIONS + 1))
  fi
done

if [[ "$VIOLATIONS" -gt 0 ]]; then
  echo "Safety boundary audit: FAILED ($VIOLATIONS violations)"
  exit 1
fi
echo "Safety boundary audit: PASSED (0 violations)"

echo ""
echo "=== All iOS checks passed ==="
