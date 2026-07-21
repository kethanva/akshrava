#!/usr/bin/env bash
# Build and push Akshrava images to Artifact Registry for the cloud/gcp/ Terraform stack.
# Prints digest-pinned image refs for terraform.tfvars (prefer over :latest).
set -euo pipefail

PROJECT_ID="${1:?usage: $0 <project_id> [region]}"
REGION="${2:-us-central1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/akshrava"

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
gcloud artifacts repositories describe akshrava --location="${REGION}" --project="${PROJECT_ID}" >/dev/null

API_TAG="${REPO}/akshrava-api:latest"
WORKER_TAG="${REPO}/akshrava-worker:latest"

docker build \
  --platform linux/amd64 \
  --build-arg INSTALL_YOLO=false \
  --build-arg INSTALL_CLOUD_PROVIDER=gcp \
  -t "${API_TAG}" \
  -f "${ROOT}/backend/Dockerfile" \
  "${ROOT}/backend"

docker build \
  --platform linux/amd64 \
  -t "${WORKER_TAG}" \
  -f "${ROOT}/backend/Dockerfile.gpu" \
  "${ROOT}/backend"

docker push "${API_TAG}"
docker push "${WORKER_TAG}"

API_DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${API_TAG}")"
WORKER_DIGEST="$(docker inspect --format='{{index .RepoDigests 0}}' "${WORKER_TAG}")"

echo "Pushed:"
echo "  ${API_TAG}"
echo "  ${WORKER_TAG}"
echo
echo "Digest-pinned values for cloud/gcp/terraform.tfvars:"
echo "  api_image    = \"${API_DIGEST}\""
echo "  worker_image = \"${WORKER_DIGEST}\""
echo
echo "Next: ./scripts/gcp_migrate_then_deploy.sh ${PROJECT_ID} ${REGION}"
