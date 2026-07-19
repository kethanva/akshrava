#!/usr/bin/env bash
# Exercise both the field and GPU-worker paths without touching a real deployment environment.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
fixture_dir=$(mktemp -d)
trap 'rm -rf "$fixture_dir"' EXIT

mkdir -p "$fixture_dir/models"
printf 'approved model fixture\n' >"$fixture_dir/models/approved.pt"
approved_sha=$(shasum -a 256 "$fixture_dir/models/approved.pt" | awk '{print $1}')
cat >"$fixture_dir/.env" <<'EOF'
POSTGRES_PASSWORD=preflight_password_0123456789_abcd
JWT_ALGORITHM=RS256
JWT_PUBLIC_KEY_FILE=/run/secrets/jwt/device-public.pem
GRAFANA_PASSWORD=preflight_grafana_0123456789_abcd
REDIS_PASSWORD=preflight_redis_secret_0123456789_abcd
DOMAIN=pilot.internal.invalid
AKSHRAVA_ENV=pilot
DETECTOR=ultralytics
DEV_AUTH_BYPASS=false
INSTALL_YOLO=true
MODEL_DIR=./models
YOLO_WEIGHTS=/models/approved.pt
YOLO_WEIGHTS_SHA256=__APPROVED_SHA__
CLOUD_FALLBACK_PROVIDER=none
REMOTE_WORKER_SECRET=preflight_remote_worker_secret_012345
EOF
sed -i.bak "s/__APPROVED_SHA__/$approved_sha/" "$fixture_dir/.env"

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

cat >>"$fixture_dir/.env" <<'EOF'
REMOTE_INFERENCE_URL=
REMOTE_INFERENCE_REGISTRY_JSON=[{"id":"gpu-a","url":"https://gpu-a.pilot.internal/v1/infer"},{"id":"gpu-b","url":"https://gpu-b.pilot.internal/v1/infer","enabled":false}]
EOF
"$repo_root/scripts/cloud_preflight.sh" "$fixture_dir/.env"
