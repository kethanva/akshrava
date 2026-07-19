#!/usr/bin/env bash
# Migrate-then-deploy gate for the GCP stack: Alembic must succeed before traffic moves.
set -euo pipefail

PROJECT_ID="${1:?usage: $0 <project_id> [region]}"
REGION="${2:-us-central1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Applying Terraform (creates/updates migrate job + API revision)..."
terraform -chdir="${ROOT}/gcp" apply -auto-approve

echo "Running Cloud Run migrate job (blocks until Alembic succeeds)..."
gcloud run jobs execute akshrava-migrate \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait

echo "Migration succeeded. Cloud Run API may now serve traffic on the new revision."
echo "Confirm: gcloud run services describe akshrava-api --region=${REGION} --project=${PROJECT_ID}"
