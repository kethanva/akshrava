#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
# Tests exercise the explicit transport-only profile and must not inherit an operator's shell
# deployment settings (for example DEV_AUTH_BYPASS=false or DETECTOR=ultralytics).
export DEV_AUTH_BYPASS=true
export DETECTOR=noop
python -m pip install --upgrade 'pip<25'
python -m pip install '.[dev]'
pytest -q
ruff check akshrava_backend tests
