#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Akshrava Unified E2E Test Suite Runner
# ==============================================================================
# Executes:
# 1. Strict Safety Boundary Compliance Audit (Object/Vehicle Awareness ONLY)
# 2. Backend E2E Test Suite (Pytest with Python 3.10+)
# 3. Android JVM E2E & Unit Test Suite (Gradle testDebugUnitTest)
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "=== Starting Akshrava E2E Test Execution ==="
echo "Repo Root: ${REPO_ROOT}"

# ------------------------------------------------------------------------------
# Step 1: Strict Safety Boundary Audit
# ------------------------------------------------------------------------------
echo ""
echo "--- Step 1: Enforcing Strict Safety Boundary Compliance Audit ---"
FORBIDDEN_PATTERN="navigate|turn left|turn right|walk forward|cross now|safe to cross|collision imminent|approaching at|closing speed|clear path|path is clear|way is clear"

# Audit source files (excluding AGENTS.md, PROJECT.md, TEST_INFRA.md safety warnings)
VIOLATIONS=$(grep -rnE -i "${FORBIDDEN_PATTERN}" \
  "${REPO_ROOT}/backend/akshrava_backend" \
  "${REPO_ROOT}/android/app/src/main" 2>/dev/null || true)

if [[ -n "${VIOLATIONS}" ]]; then
  echo "❌ SAFETY VIOLATION DETECTED! Prohibited navigation/crossing/collision/safe terms found:" >&2
  echo "${VIOLATIONS}" >&2
  exit 1
fi
echo "✅ Safety Boundary Audit Passed: 0 forbidden terms found in production source."

# ------------------------------------------------------------------------------
# Step 2: Backend E2E & Integrated Test Suite
# ------------------------------------------------------------------------------
echo ""
echo "--- Step 2: Executing Backend Test Suite ---"
cd "${REPO_ROOT}/backend"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Error: Python 3.10+ is required (set PYTHON_BIN to python3.12 or python3.10+)." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "Creating backend virtualenv with $PYTHON_BIN..."
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

if ! python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Recreating backend/.venv with $PYTHON_BIN..."
  deactivate 2>/dev/null || true
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv
  source .venv/bin/activate
fi

export DEV_AUTH_BYPASS=true
export DETECTOR=noop

python -m pip install --upgrade 'pip>=26.1.2' 'setuptools>=83' >/dev/null 2>&1 || true
python -m pip install -e '.[dev]' >/dev/null 2>&1 || python -m pip install '.[dev]'

pytest -q
ruff check akshrava_backend tests
echo "✅ Backend Test Suite Passed."

# ------------------------------------------------------------------------------
# Step 3: Android JVM Test Suite
# ------------------------------------------------------------------------------
echo ""
echo "--- Step 3: Executing Android JVM Test Suite ---"
cd "${REPO_ROOT}/android"
./gradlew :app:testDebugUnitTest --quiet
echo "✅ Android JVM Test Suite Passed."

echo ""
echo "=== All Akshrava E2E & Baseline Tests Completed Successfully ==="
exit 0
