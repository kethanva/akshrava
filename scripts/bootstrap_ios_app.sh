#!/usr/bin/env bash
# Generate the Xcode app that depends on the local Akshrava SPM package.
# Spec is ios/AkshravaApp/project.yml (checked in). The .xcodeproj is generated, never committed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="$ROOT/ios/AkshravaApp"
PKG_DIR="$ROOT/ios/Akshrava"
PROJ="$ROOT/ios/AkshravaApp.xcodeproj"

if [[ ! -f "$APP_DIR/project.yml" ]]; then
  echo "ERROR: missing $APP_DIR/project.yml (the checked-in xcodegen spec)." >&2
  exit 1
fi

if [[ ! -d "$PKG_DIR" ]]; then
  echo "ERROR: missing SPM package at $PKG_DIR" >&2
  exit 1
fi

if ! command -v xcodegen >/dev/null 2>&1; then
  # Do NOT regenerate project.yml here — that previously overwrote the tracked spec.
  echo "ERROR: xcodegen not installed; cannot generate $PROJ" >&2
  echo "Install with: brew install xcodegen && $0" >&2
  exit 1
fi

cd "$APP_DIR"
xcodegen generate --spec project.yml --project "$ROOT/ios"

if [[ ! -d "$PROJ" ]]; then
  echo "ERROR: xcodegen finished but $PROJ was not created" >&2
  exit 1
fi

echo "Generated $PROJ"
echo "Open with: open $PROJ"
