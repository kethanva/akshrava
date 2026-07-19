#!/usr/bin/env bash
# Build and push Akshrava images to Artifact Registry for the gcp/ Terraform stack.
set -euo pipefail

PROJECT_ID="${1:?usage: $0 <project_id> [region]}"
REGION="${2:-us-central1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/akshrava"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
gcloud artifacts repositories describe akshrava --location="${REGION}" --project="${PROJECT_ID}" >/dev/null

API_IMAGE="${REPO}/akshrava-api:latest"
WORKER_IMAGE="${REPO}/akshrava-worker:latest"

docker build \
  --build-arg INSTALL_YOLO=false \
  --build-arg INSTALL_CLOUD_PROVIDER=gcp \
  -t "${API_IMAGE}" \
  -f "${ROOT}/backend/Dockerfile" \
  "${ROOT}/backend"

docker build \
  -t "${WORKER_IMAGE}" \
  -f "${ROOT}/backend/Dockerfile.gpu" \
  "${ROOT}/backend"

docker push "${API_IMAGE}"
docker push "${WORKER_IMAGE}"

echo "Pushed:"
echo "  ${API_IMAGE}"
echo "  ${WORKER_IMAGE}"
echo "Next: terraform -chdir=gcp apply && gcloud run jobs execute akshrava-migrate --region ${REGION} --wait"
