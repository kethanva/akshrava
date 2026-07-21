#!/usr/bin/env bash
# Preflight for the cloud/gcp/ Terraform stack before apply / detector=remote.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/cloud/gcp"

CI_MODE=false
if [[ "${1:-}" == "--ci" ]]; then
  CI_MODE=true
  shift
fi
if [[ $# -ne 0 ]]; then
  echo "usage: $0 [--ci]" >&2
  exit 2
fi

if ! command -v terraform >/dev/null 2>&1; then
  echo "terraform is required" >&2
  exit 1
fi

# A clean CI checkout intentionally has neither the ignored tfvars file nor private PKI PEMs.
# Validate Terraform syntax and provider wiring with an ephemeral bootstrap-PKI configuration;
# deployment preflight (the default mode) still verifies the operator's real external PKI.
if [[ "$CI_MODE" == true ]]; then
  export TF_VAR_project_id="${TF_VAR_project_id:-akshrava-ci-validation}"
  export TF_VAR_manage_pki_in_terraform=true
fi

terraform fmt -check -recursive
terraform init -backend=false -input=false >/dev/null
terraform validate

if [[ "$CI_MODE" == true ]]; then
  echo "gcp CI validation ok (ephemeral bootstrap PKI; no deployment credentials or secrets used)"
elif [[ -f terraform.tfvars ]]; then
  python3 - <<'PY'
from pathlib import Path
import sys

text = Path("terraform.tfvars").read_text(encoding="utf-8")
vals = {}
for line in text.splitlines():
    line = line.split("#", 1)[0].strip()
    if not line or "=" not in line:
        continue
    key, raw = line.split("=", 1)
    vals[key.strip()] = raw.strip()

detector = vals.get("detector", '"noop"').strip('"').strip("'")
sha = vals.get("yolo_weights_sha256", '""').strip('"').strip("'")
if detector == "remote" and len(sha) != 64:
    print("detector=remote requires yolo_weights_sha256 (64 hex) in terraform.tfvars", file=sys.stderr)
    sys.exit(1)

allow_public = vals.get("api_allow_unauthenticated", "false").lower() in {"true", "1"}
invokers = vals.get("api_invoker_members", "[]")
has_invoker = (
    invokers not in {"[]", "", "null"}
    and ("serviceAccount:" in invokers or "group:" in invokers or "user:" in invokers)
)
if not allow_public and not has_invoker:
    print(
        "Phone WSS reachability: set api_invoker_members (edge SA/group) or "
        "api_allow_unauthenticated=true with a documented public edge.",
        file=sys.stderr,
    )
    sys.exit(1)

manage_pki = vals.get("manage_pki_in_terraform", "false").lower() in {"true", "1"}
if not manage_pki:
    pem_files = {
        "jwt_public_key_pem": "jwt-public.pem",
        "jwt_private_key_pem": "jwt-private.pem",
        "worker_ca_cert_pem": "worker-ca.pem",
        "worker_server_cert_pem": "worker-server-cert.pem",
        "worker_server_key_pem": "worker-server-key.pem",
        "worker_client_cert_pem": "worker-client-cert.pem",
        "worker_client_key_pem": "worker-client-key.pem",
    }
    missing = []
    for k, filename in pem_files.items():
        has_var = k in vals and vals[k] not in {"", '""', "''"}
        has_file = Path("pki", filename).is_file()
        if not has_var and not has_file:
            missing.append(k)
    if missing:
        print(
            "manage_pki_in_terraform=false requires PEM vars in tfvars or files in cloud/gcp/pki/: %s" % ", ".join(missing),
            file=sys.stderr,
        )
        sys.exit(1)

print("gcp preflight ok (detector=%s, public_invoker=%s)" % (detector, allow_public))
PY
else
  echo "gcp preflight ok (no terraform.tfvars yet; copy terraform.tfvars.example before apply)"
fi
