#!/usr/bin/env bash
# Preflight for the gcp/ Terraform stack before apply / detector=remote.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/gcp"

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform is required" >&2
  exit 1
fi

terraform fmt -check -recursive
terraform init -backend=false -input=false >/dev/null
terraform validate

if [[ -f terraform.tfvars ]]; then
  # shellcheck disable=SC1091
  detector="$(python3 - <<'PY'
from pathlib import Path
text = Path("terraform.tfvars").read_text(encoding="utf-8")
detector = "noop"
for line in text.splitlines():
    line = line.split("#", 1)[0].strip()
    if line.startswith("detector"):
        detector = line.split("=", 1)[1].strip().strip('"').strip("'")
print(detector)
PY
)"
  sha="$(python3 - <<'PY'
from pathlib import Path
text = Path("terraform.tfvars").read_text(encoding="utf-8")
sha = ""
for line in text.splitlines():
    line = line.split("#", 1)[0].strip()
    if line.startswith("yolo_weights_sha256"):
        sha = line.split("=", 1)[1].strip().strip('"').strip("'")
print(sha)
PY
)"
  if [[ "$detector" == "remote" && "${#sha}" -ne 64 ]]; then
    echo "detector=remote requires yolo_weights_sha256 (64 hex) in terraform.tfvars" >&2
    exit 1
  fi
  echo "gcp preflight ok (detector=${detector})"
else
  echo "gcp preflight ok (no terraform.tfvars yet; copy terraform.tfvars.example before apply)"
fi
