# Enterprise production readiness — remediation

Tracks the Top 5 items from the 2026-07-20 Enterprise Production Readiness Review against the GCP
supervised-pilot codebase (`gcp/` + Cloud Run API + GCE worker).

## Verdict after remediation

| Item | Severity | Remediation | Live default |
|---|---|---|---|
| 1. Regional MIG for workers | HIGH (SPOF) | `gcp/worker_ha.tf` | **off** (`enable_worker_ha=false`) — enable after quota review |
| 2. Cloud Armor + LB | HIGH | `gcp/cloud_armor.tf` | **off** — needs DNS domain |
| 3. API soft backpressure | CRITICAL | `WorkerSaturatedError` → soft `worker_saturated`; Android soft-shed | **on** in API/Android code |
| 4. JSON logs + SLI/SLO | MEDIUM | `logging_util.py` + `gcp/monitoring.tf` | JSON **on**; saturation alert **opt-in** |
| 5. JWT rotation | MEDIUM | dual-key verify + `scripts/rotate_jwt_rs256.sh` + previous PEM mount | **on** (previous seeded = current until first rotate) |

**Still deferred (explicitly out of Top-5 scope for this pass):** Redis Streams / queue-depth MIG
autoscaling, chaos/load suites at 1000 clients, Blue/Green worker cutover automation, token CRL.

**Unsupervised field production remains blocked** until MIG + Armor are enabled for the target
environment and E2E gates pass. Soft backpressure alone does not remove the single-zone worker SPOF.

## Code / IaC map

| Concern | Primary files |
|---|---|
| Soft shed 503 | `backend/akshrava_backend/detector.py`, `main.py`, `metrics.py` |
| W3C tracing | `backend/akshrava_backend/tracing.py` (+ worker log correlation) |
| JSON logging | `backend/akshrava_backend/logging_util.py` |
| Dual-key JWT | `backend/akshrava_backend/auth.py`, `gcp/secrets.tf`, `gcp/app.tf` |
| Android backpressure | `android/.../ProtocolClient.kt` (`max_in_flight`, soft errors) |
| MIG / Armor | `gcp/worker_ha.tf`, `gcp/cloud_armor.tf`, `gcp/variables.tf` |
| Monitoring | `gcp/monitoring.tf` |
| Rotation runbook | `scripts/rotate_jwt_rs256.sh` |
| Enablement profile | `gcp/terraform.tfvars.production.example` |

## Enable HA + Armor (operator checklist)

1. Confirm regional CPU/GPU quota for `worker_ha_target_size`.
2. Choose a domain; create DNS A record pointing at the LB IP **after** first Armor apply (or plan
   two applies: LB IP → DNS → cert).
3. Copy flags from `gcp/terraform.tfvars.production.example` into `gcp/terraform.tfvars`.
4. `cd gcp && terraform plan` — review destroy/replace of the single worker VM.
5. Apply, then run `scripts/e2e_gcp_pilot.sh` and `scripts/e2e_android_protocol_gcp.sh`.

## JWT rotation checklist

```bash
./scripts/rotate_jwt_rs256.sh [project_id]
# Redeploy API so mounts refresh if needed
./scripts/build_gcp_images.sh
# Re-mint field tokens
./scripts/mint_device_token_gcp.sh …
```

During cutover the API accepts tokens signed by **current** or **previous** public keys.

## Verification

```bash
./scripts/test_backend.sh
# Optional: terraform validate in gcp/
cd gcp && terraform validate
```
