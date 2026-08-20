#!/usr/bin/env bash
# Migrate-then-deploy gate for the GCP stack: Alembic must succeed before traffic moves.
set -euo pipefail

PROJECT_ID="${1:?usage: $0 <project_id> [region]}"
REGION="${2:-us-central1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Phase 1/3: converge infrastructure + migrate job at the new image (API revision untouched)"
"${ROOT}/scripts/gcp_preflight.sh"
terraform -chdir="${ROOT}/cloud/gcp" init -input=false
# -target pulls in the job's transitive dependencies (Cloud SQL, VPC connector, secrets, AR)
# but deliberately excludes google_cloud_run_v2_service.api, so no traffic can move yet.
terraform -chdir="${ROOT}/cloud/gcp" apply -input=false -auto-approve \
  -target=google_cloud_run_v2_job.migrate

echo "Phase 2/3: Alembic to head (blocks; non-zero exit stops the deploy here)"
gcloud run jobs execute akshrava-migrate \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --wait

echo "Phase 3/3: full apply — new API revision + traffic"
terraform -chdir="${ROOT}/cloud/gcp" apply -input=false -auto-approve

echo "Migration succeeded. Cloud Run API may now serve traffic on the new revision."
echo "Confirm: gcloud run services describe akshrava-api --region=${REGION} --project=${PROJECT_ID}"
echo "See OPERATIONS.md: between phase 2 and phase 3 the old revision's expected schema no longer"
echo "matches the DB. Rollback means image + alembic downgrade together, never image-only."
