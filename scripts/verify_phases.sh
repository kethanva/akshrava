#!/usr/bin/env bash
# Repository verification baseline (bench/CI). Not field-use approval.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ ! -x .venv/bin/python ]]; then
  echo "Creating backend/.venv via scripts/test_backend.sh dependency install path..." >&2
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade 'pip<25'
  python -m pip install '.[dev]'
else
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
