#!/usr/bin/env bash
# Rotate Akshrava RS256 device JWT keys in GCP Secret Manager with a dual-key cutover window.
#
# Steps:
#   1. Generate a new RSA keypair locally
#   2. Publish NEW public key as akshrava-jwt-public (latest)
#   3. Keep PREVIOUS public key available as akshrava-jwt-public-previous for API dual-verify
#   4. Publish NEW private key as akshrava-jwt-private (latest) for mint scripts
#   5. Redeploy / bounce Cloud Run so mounts refresh (or rely on secret volume latest)
#
# Usage:
#   ./scripts/rotate_jwt_rs256.sh [project_id]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -f "$ROOT/.env" ]; then set -a; source "$ROOT/.env"; set +a; fi
PROJECT_ID="${1:-${AKSHRAVA_PROJECT_ID:-<your-gcp-project-id>}}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> generating new RS256 keypair in $TMP"
openssl genrsa -out "$TMP/jwt-private.pem" 2048
openssl rsa -in "$TMP/jwt-private.pem" -pubout -out "$TMP/jwt-public.pem"
chmod 600 "$TMP/jwt-private.pem"

echo "==> snapshotting current public key to previous (if present)"
if gcloud secrets describe akshrava-jwt-public --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets versions access latest --secret=akshrava-jwt-public --project="$PROJECT_ID" \
    >"$TMP/jwt-public-previous.pem" || true
  if [[ -s "$TMP/jwt-public-previous.pem" ]]; then
    if ! gcloud secrets describe akshrava-jwt-public-previous --project="$PROJECT_ID" >/dev/null 2>&1; then
      gcloud secrets create akshrava-jwt-public-previous --project="$PROJECT_ID" --replication-policy=automatic
    fi
    gcloud secrets versions add akshrava-jwt-public-previous --project="$PROJECT_ID" \
      --data-file="$TMP/jwt-public-previous.pem"
  fi
fi

echo "==> publishing new public + private keys"
gcloud secrets versions add akshrava-jwt-public --project="$PROJECT_ID" --data-file="$TMP/jwt-public.pem"
gcloud secrets versions add akshrava-jwt-private --project="$PROJECT_ID" --data-file="$TMP/jwt-private.pem"

cat <<EOF
==> rotation published.
Next:
  1. Ensure Cloud Run mounts JWT_PUBLIC_KEY_PREVIOUS_FILE from akshrava-jwt-public-previous
     (see cloud/gcp/app.tf / secrets.tf dual-key mount).
  2. Redeploy API: ./scripts/build_gcp_images.sh && terraform apply (or gcloud run services update).
  3. Mint new device tokens with mint_device_token_gcp.sh for field phones.
  4. After all devices are re-provisioned, disable previous-key mount.
EOF
