#!/usr/bin/env bash
# Repository verification baseline (bench/CI). Not field-use approval.
# There is no separate phase0_replay / labelled regression-clip binary in this tree yet.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ ! -x .venv/bin/python ]]; then
  echo "backend/.venv missing; run ./scripts/test_backend.sh once to create it" >&2
  exit 1
fi

# Tests exercise the explicit transport-only profile and must not inherit an operator's shell
# deployment settings (for example DEV_AUTH_BYPASS=false or DETECTOR=ultralytics).
export DEV_AUTH_BYPASS=true
export DETECTOR=noop
export PYTHONPATH=.

.venv/bin/python -m pytest -q
if [[ -x .venv/bin/ruff ]]; then
  .venv/bin/ruff check akshrava_backend tests
fi
# Optional coverage (measured once when needed; do not invent percentages in docs):
# DEV_AUTH_BYPASS=true DETECTOR=noop .venv/bin/python -m pytest --cov=akshrava_backend --cov-report=term-missing -q
