#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../backend"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10+ is required (set PYTHON_BIN to a supported interpreter)." >&2
  exit 1
fi
if [[ ! -d .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
if ! python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "backend/.venv uses Python older than 3.10; recreate it with PYTHON_BIN=<python3.10+>." >&2
  exit 1
fi
# Tests exercise the explicit transport-only profile and must not inherit an operator's shell
# deployment settings (for example DEV_AUTH_BYPASS=false or DETECTOR=ultralytics).
export DEV_AUTH_BYPASS=true
export DETECTOR=noop
python -m pip install --upgrade 'pip>=26.1.2' 'setuptools>=83'
python -m pip install '.[dev]'
pytest -q
ruff check akshrava_backend tests
