#!/usr/bin/env bash
# Render every supported local Compose profile with ephemeral CI-safe values.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$ROOT/cloud/local/docker-compose.yml"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required to validate Compose configuration" >&2
  exit 1
fi

random_secret() {
  openssl rand -hex 24
}

render() {
  POSTGRES_PASSWORD="$(random_secret)" \
  JWT_SECRET="$(random_secret)" \
  JWT_ALGORITHM=RS256 \
  JWT_PUBLIC_KEY_FILE=/run/secrets/jwt/device-public.pem \
  JWT_KEY_DIR=../secrets/jwt \
  METRICS_SCRAPE_TOKEN="$(random_secret)" \
  DOMAIN=example.org \
  GRAFANA_PASSWORD="$(random_secret)" \
  REDIS_PASSWORD="$(random_secret)" \
  ALERT_WEBHOOK_URL=https://hooks.example.com/ci \
  docker compose -f "$COMPOSE_FILE" "$@" config --quiet
}

render --profile control-plane --profile edge --profile monitoring
render --profile gpu-worker
echo "Compose configuration validation passed"
