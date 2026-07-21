#!/usr/bin/env bash
# Repository verification baseline (bench/CI). Not field-use approval.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10+ is required (set PYTHON_BIN to a supported interpreter)." >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating backend/.venv via scripts/test_backend.sh dependency install path..." >&2
  "$PYTHON_BIN" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade 'pip>=26.1.2' 'setuptools>=83'
  python -m pip install '.[dev]'
else
  if ! .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "backend/.venv uses Python older than 3.10; recreate it with PYTHON_BIN=<python3.10+>." >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

# Tests exercise the explicit transport-only profile and must not inherit an operator's shell
# deployment settings (for example DEV_AUTH_BYPASS=false or DETECTOR=ultralytics).
export DEV_AUTH_BYPASS=true
export DETECTOR=noop
export AKSHRAVA_ENV="${AKSHRAVA_ENV:-development}"
export PYTHONPATH=.

python -m pytest -q
if command -v ruff >/dev/null 2>&1 || [[ -x .venv/bin/ruff ]]; then
  .venv/bin/ruff check akshrava_backend tests
fi

echo "Phase-0 policy replay is included in pytest (tests/test_phase0_replay.py)."
