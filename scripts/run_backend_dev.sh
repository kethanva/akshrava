#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade 'pip<25'
python -m pip install '.[dev]'
export DEV_AUTH_BYPASS=true
export DETECTOR=ultralytics
exec uvicorn akshrava_backend.main:app --host 0.0.0.0 --port 8000 --reload
