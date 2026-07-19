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
REDIS_PASSWORD=preflight_redis_secret_0123456789_abcd
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

# Remote inference must not pass a production-like preflight with only HTTPS and an HMAC secret:
# the control plane also needs its mTLS client identity.
cat >>"$fixture_dir/.env" <<'EOF'
DETECTOR=remote
REMOTE_INFERENCE_URL=https://gpu.pilot.internal/v1/infer
REMOTE_TLS_CA_FILE=/run/secrets/worker-mtls/ca.pem
REMOTE_TLS_CLIENT_CERT_FILE=/run/secrets/worker-mtls/client.pem
REMOTE_TLS_CLIENT_KEY_FILE=/run/secrets/worker-mtls/client-key.pem
EOF
"$repo_root/scripts/cloud_preflight.sh" "$fixture_dir/.env"
