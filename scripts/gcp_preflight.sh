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
    required = [
        "jwt_public_key_pem",
        "jwt_private_key_pem",
        "worker_ca_cert_pem",
        "worker_server_cert_pem",
        "worker_server_key_pem",
        "worker_client_cert_pem",
        "worker_client_key_pem",
    ]
    missing = [k for k in required if k not in vals or vals[k] in {"", '""', "''"}]
    if missing:
        print(
            "manage_pki_in_terraform=false requires PEM vars: %s" % ", ".join(missing),
            file=sys.stderr,
        )
        sys.exit(1)

print("gcp preflight ok (detector=%s, public_invoker=%s)" % (detector, allow_public))
PY
else
  echo "gcp preflight ok (no terraform.tfvars yet; copy terraform.tfvars.example before apply)"
fi
