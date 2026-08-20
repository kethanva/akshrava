# OPERATIONS.md

Runbook for the GCP control plane. This is not field-use approval.

## Current pilot configuration

The supervised pilot is a signed-off trade-off, not an accidental gap:

- **Worker:** a single VM (`enable_worker_ha = false` in the live tfvars) unless an operator has explicitly flipped the regional MIG. Variable *defaults* prefer HA; a deployment whose tfvars opt out is still a zonal SPOF for inference. See `cloud/gcp/worker_ha.tf`.
- **Cloud Armor:** off. Cloud Run's `*.run.app` URL, `INGRESS_TRAFFIC_ALL`, and JWT on the WebSocket are the auth boundary. Enabling Armor restricts ingress to the load balancer. See `cloud/gcp/cloud_armor.tf`.
- **Public invoker:** `api_allow_unauthenticated` may be true on the pilot edge. Prefer `api_invoker_members` (edge SA/group) for production.

Do not assume HA, Armor, or a private invoker from this repository's defaults. Read the applied tfvars.

## Migrate before traffic

`scripts/gcp_migrate_then_deploy.sh` is three phases:

1. Targeted `terraform apply -target=google_cloud_run_v2_job.migrate` so the migrate job and its dependencies (Cloud SQL, secrets, Artifact Registry) converge on the new image **without** moving API traffic.
2. `gcloud run jobs execute akshrava-migrate --wait`. Alembic must reach head. A non-zero exit **stops the deploy**.
3. Full `terraform apply` — new Cloud Run API revision plus traffic.

The API container's **startup probe is `/readyz`** (schema + DB + Redis). `/livez` is liveness only and does not prove schema. `store.initialize()` → `verify_schema()` fail-closes a revision whose `alembic_version` does not match `DATABASE_SCHEMA_REVISION`.

**Between phase 2 and phase 3** the *old* revision's expected schema no longer matches the database. Warm instances keep serving (`min_instance_count = 1`). A cold start of the old revision fails readiness. The window is a single apply.

## Rollback

Rollback is **image + `alembic downgrade` together**, never “redeploy the previous image only”. An old image against a forwarded schema fails `/readyz` and takes phones not-ready. Forward-fix (migrate again, then apply) is the other legal path.

## Revoke a device

`store.revoke_device` writes a revocation that replicas cache in Redis (`revocation:{device_id}`: 15 s positive / 5 s negative TTL). The next **frame or ping** on a live socket closes **4403** with JSON `device_revoked`. A connect from an already-revoked token **accepts** the socket long enough to send that same JSON, then closes 4403 — closing *before* accept collapsed to HTTP 403 and phones could not tell revoke from a bad token. Ping is in the revoke path so a quiet session cannot keep walking on a revoked token until the next capture.

## Rotate the JWT signing key

Use `scripts/rotate_jwt_rs256.sh`. Cloud Run mounts the previous public key via `JWT_PUBLIC_KEY_PREVIOUS_FILE` (`cloud/gcp/app.tf`) so in-flight device tokens verify during cutover. Re-mint device tokens after the old key is retired. Do not leave both keys in Terraform state for a production project (`manage_pki_in_terraform = false`; PEMs live in `cloud/gcp/pki/`).

## Who is paged

`monitoring_notification_channels` must be non-empty in **production** (Terraform precondition on `api_uptime` plus `scripts/gcp_preflight.sh`). Policies:

| Policy | Meaning |
|---|---|
| `akshrava-api-uptime-failed` | `/readyz` uptime check failed for 5 minutes. Phones cannot start sessions. |
| `akshrava-worker-saturated-elevated` | Soft-sheds from a saturated detector. Optional (`enable_worker_saturation_log_metric`). |
| `akshrava-phone-result-acknowledgements-missing` | Results were sent and never acknowledged. This is **receipt/freshness processing, not TTS playback**. |

An alert policy with no channel fires into an empty room.

## Remote state

Copy `cloud/gcp/backend.tf.example` to `backend.tf` for a real project. Use a CMEK-encrypted GCS bucket. CI and local validate use `terraform init -backend=false`; do not commit a live `backend.tf`. If state ever left the bucket, treat JWT and worker keys that were in it as compromised and rotate.

## Expected metric shifts after this release

`akshrava_late_suppressed_total` will step up: capture-to-receive age now counts toward the speech freshness budget. Nothing that was previously *spoken* becomes silent — the phone already dropped those results via `capture_mono_ms`. What stops is spending an alert cooldown on a hazard nobody could hear.

New counters: `akshrava_late_capture_suppressed_total`, `akshrava_alerts_rate_limited_total`, `akshrava_alerts_debounced_total`, `akshrava_control_messages_rejected_total`, `akshrava_session_superseded_total`.
