#!/usr/bin/env bash
# Copy pinned YOLO weights onto the GCP GPU worker and verify SHA-256 before detector=remote.
set -euo pipefail

usage() {
  echo "usage: $0 <project_id> <zone> <local_weights_path> <expected_sha256>" >&2
  echo "  copies weights to akshrava-gpu-worker:/var/lib/akshrava/models/yolo11s.pt" >&2
  exit 2
}

PROJECT_ID="${1:-}"
ZONE="${2:-}"
LOCAL_PATH="${3:-}"
EXPECTED_SHA="${4:-}"
[[ -n "$PROJECT_ID" && -n "$ZONE" && -n "$LOCAL_PATH" && -n "$EXPECTED_SHA" ]] || usage
[[ -f "$LOCAL_PATH" ]] || { echo "weights file not found: $LOCAL_PATH" >&2; exit 1; }
[[ "${#EXPECTED_SHA}" -eq 64 ]] || { echo "expected_sha256 must be 64 hex chars" >&2; exit 1; }

ACTUAL="$(shasum -a 256 "$LOCAL_PATH" | awk '{print $1}')"
if [[ "$ACTUAL" != "$EXPECTED_SHA" ]]; then
  echo "local SHA mismatch: got $ACTUAL expected $EXPECTED_SHA" >&2
  exit 1
fi

INSTANCE="${INSTANCE:-akshrava-gpu-worker}"
REMOTE_DIR="/var/lib/akshrava/models"
REMOTE_PATH="${REMOTE_DIR}/yolo11s.pt"

gcloud compute ssh "$INSTANCE" --project="$PROJECT_ID" --zone="$ZONE" --command="sudo mkdir -p ${REMOTE_DIR} && sudo chmod 755 ${REMOTE_DIR}"
gcloud compute scp "$LOCAL_PATH" "${INSTANCE}:/tmp/yolo11s.pt" --project="$PROJECT_ID" --zone="$ZONE"
gcloud compute ssh "$INSTANCE" --project="$PROJECT_ID" --zone="$ZONE" --command="
  set -euo pipefail
  echo ${EXPECTED_SHA}  /tmp/yolo11s.pt | sha256sum -c -
  sudo mv /tmp/yolo11s.pt ${REMOTE_PATH}
  sudo chmod 644 ${REMOTE_PATH}
  echo installed ${REMOTE_PATH}
"

echo "Weights installed and SHA verified on ${INSTANCE}."
echo "Next: set detector=remote yolo_weights_sha256=${EXPECTED_SHA} in gcp/terraform.tfvars and terraform apply."
