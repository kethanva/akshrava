#!/usr/bin/env bash
# Exercise both the field and GPU-worker paths without touching a real deployment environment.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
fixture_dir=$(mktemp -d)
trap 'rm -rf "$fixture_dir"' EXIT

mkdir -p "$fixture_dir/models"
touch "$fixture_dir/models/approved.pt"
cat >"$fixture_dir/.env" <<'EOF'
POSTGRES_PASSWORD=preflight_password_0123456789_abcd
JWT_SECRET=preflight_jwt_secret_0123456789_abcd
GRAFANA_PASSWORD=preflight_grafana_0123456789_abcd
DOMAIN=pilot.internal.invalid
AKSHRAVA_ENV=pilot
DETECTOR=ultralytics
DEV_AUTH_BYPASS=false
INSTALL_YOLO=true
MODEL_DIR=./models
YOLO_WEIGHTS=/models/approved.pt
CLOUD_FALLBACK_PROVIDER=none
REMOTE_WORKER_SECRET=preflight_remote_worker_secret_012345
EOF

bash -n "$repo_root/scripts/cloud_preflight.sh"
"$repo_root/scripts/cloud_preflight.sh" "$fixture_dir/.env" --field
"$repo_root/scripts/cloud_preflight.sh" "$fixture_dir/.env" --gpu-worker
