# Deployment guide

This repository supports a single-host deployment and a split deployment. In the split form, the
**control plane** owns phone WebSockets, authentication, alert scoring and PostgreSQL; the
**GPU worker** accepts only a short-lived HMAC-authenticated JPEG from that control plane and
returns detector boxes. It has no database, phone endpoint, device token or alert history.

## Before deployment

- Keep `DETECTOR=noop` until the model licence, exact model file/hash, target-device benchmark,
  labelled evaluation and controlled-course gate are approved.
- Set unique, high-entropy `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, and Grafana credentials. In
  pilot/production configure `JWT_ALGORITHM=RS256` and mount only `JWT_PUBLIC_KEY_FILE` on the API;
  keep the matching private key solely on the provisioning workstation. Never use examples.
- Set `AKSHRAVA_ENV=pilot` or `production`; the service rejects `DEV_AUTH_BYPASS=true` outside
  an explicit `development` environment.
- Provision a domain and DNS before enabling Caddy. The public phone endpoint must be WSS.
- Do not deploy a phone without its calibration/provisioning and supervised-trial sign-offs.

## Control-plane deployment

```bash
cd infra
cp .env.example .env
# Set passwords, RS256 public-key path, and DOMAIN in .env.
../scripts/cloud_preflight.sh .env
docker compose --env-file .env --profile control-plane --profile edge up -d --build
```

The one-shot `migrate` role runs Alembic before the API starts. Application processes perform no
schema mutations; a failed migration prevents rollout. The API is deliberately bound to
`127.0.0.1:8000`; Caddy is the public TLS/WSS endpoint. Open
only TCP 80 and 443 to the host. Confirm `/healthz` through the local API before provisioning a
device. Start the optional monitoring services only on a protected host or through an SSH tunnel:

```bash
docker compose --env-file .env --profile control-plane --profile monitoring up -d
```

Prometheus scrapes the aggregate `/metrics` endpoint on the internal Docker network. Caddy does
not publish that endpoint. Grafana provisions the Prometheus datasource and an overview dashboard;
Prometheus includes availability and freshness alert rules. Configure a notification receiver in
your operational tooling before treating those rules as pager alerts.

## GPU-worker deployment

Use a private WireGuard/overlay address **and** a mutually authenticated TLS proxy between the
hosts. Do **not** expose port 8000 to the public internet. In pilot/production, set
`REMOTE_INFERENCE_URL` to an `https://` worker endpoint; the backend rejects plaintext worker
URLs outside development and refuses to start unless `REMOTE_TLS_CA_FILE`,
`REMOTE_TLS_CLIENT_CERT_FILE`, and `REMOTE_TLS_CLIENT_KEY_FILE` are configured. The API mounts
those files read-only from `WORKER_MTLS_DIR`; the proxy must verify the client certificate.
Set the same `REMOTE_WORKER_SECRET` on both
hosts, set the control plane to `DETECTOR=remote`, and point `REMOTE_INFERENCE_URL` at the worker
`/v1/infer` endpoint. For more than one warm worker, prefer `REMOTE_INFERENCE_REGISTRY_JSON`:

```json
[{"id":"gpu-a","url":"https://gpu-a.internal/v1/infer"},{"id":"gpu-b","url":"https://gpu-b.internal/v1/infer"}]
```

The control plane uses the registry IDs for stable device-to-worker placement and then fails
through to the next warm peer after a transport failure. A comma-separated `REMOTE_INFERENCE_URL`
list is still accepted for simple deployments and is converted into static worker IDs. The
control-plane timeout defaults to 450 ms; a failed or late worker result causes the existing
fail-closed phone messaging.

On the GPU host, use the same approved model mount and only the GPU profile:

```bash
cd infra
../scripts/cloud_preflight.sh .env --gpu-worker
GPU_WORKER_BIND_ADDRESS=10.0.0.12 docker compose --env-file .env --profile gpu-worker up -d --build
```

`api-gpu` uses a CUDA PyTorch runtime and one Gunicorn worker so a single GPU model is not
replicated accidentally. In production its signed-request nonce is claimed atomically in Redis,
so separate worker replicas can reject replayed requests consistently. It refuses to start if
CUDA is unavailable. It has a health endpoint at
`/readyz` and a private metrics endpoint. Add its private endpoint to
`infra/prometheus-targets/gpu-workers.json` on the monitoring host, then reload Prometheus. The
worker uses HMAC request authentication with timestamp replay protection; network isolation and
TLS/mTLS remain mandatory deployment controls.

## Model activation

Only after the release gate is met, set `INSTALL_YOLO=true`, mount an approved model file via
`MODEL_DIR`, set `DETECTOR=ultralytics` (single-host) or `DETECTOR=remote` (split deployment),
set `YOLO_WEIGHTS_SHA256` to the approved file digest, and record it in the deployment record.
`YOLO_WEIGHTS` must resolve to that read-only mounted file. The server and GPU worker verify the
digest before loading the detector and must never download weights while serving a session.

An optional selected-provider image fallback is described in [CLOUD_IMAGE_FALLBACK.md](CLOUD_IMAGE_FALLBACK.md).
It is disabled by default and requires fresh privacy/consent, cost and latency sign-off.

For a proposed supervised field deployment, run `../scripts/cloud_preflight.sh .env --field`.
It rejects a placeholder/weak secret, bench-mode detector, incorrect model mount, and invalid
Compose configuration. It is a guardrail, not model-validation evidence.

## Backups and operations

Take an encrypted, access-controlled off-host database backup before each deployment and test a
restore regularly. The helper creates a local dump with owner-only file permissions; it does not
know any cloud-provider credentials:

```bash
../scripts/backup_postgres.sh .env /secure/backup/directory
```

`/readyz` verifies database connectivity and is used by Docker health checks. `/healthz` is only a
process liveness endpoint. Caddy exposes neither `/metrics` nor Grafana/Prometheus publicly; use
an SSH tunnel or other separately authenticated admin access.

## Schema changes

All production changes are Alembic revisions under `backend/migrations`. Rehearse each upgrade and
downgrade against a restored backup, run `alembic upgrade head` as the one-shot deploy role, and
record the revision in the release log. Do not add runtime DDL to API startup.

There is no enabled Android fallback. If the network, control plane, or GPU worker fails, the app
must say vision assistance is unavailable and the user must continue with a cane or guide.

## Calibration profile activation

The backend records calibration IDs on devices, but it loads geometry only from a separately
verified `calibration_profiles` record (focal length and mounted camera height). Unknown or
unverified profiles keep `range_valid=false`. Provisioning must create, verify and audit that
record only after the device/mount controlled-course gate; do not treat a string calibration ID as
measurement evidence.

## Scale and failover boundary

Redis now provides atomic session admission, nonce claims and per-device frame-rate limits across
API/worker replicas. The static inference registry provides stable device-to-worker placement and
warm-peer fail-through, but it does not by itself provide automatic health re-pointing. Production
still requires authenticated registry updates, health-checked routing, a warm spare, and a
rehearsed failover/restore drill.

## GCP (Cloud Run + private GPU worker)

The [`gcp/`](../gcp/) Terraform module provisions the live Google Cloud path that matches the app
fail-closed contract:

| Resource | Role |
|---|---|
| Cloud Run `akshrava-api` | Phone WSS control plane (RS256 JWT, 1h request timeout) |
| Cloud Run Job `akshrava-migrate` | Alembic `upgrade head` before serving |
| Cloud SQL Postgres (private IP) | Alert/device/audit store — no raw frames |
| Memorystore Redis | Session admission + worker nonce claims |
| GCE `g2-standard-4` + L4 | GPU worker (only when `detector=remote`) |
| Caddy on worker `:8443` | HTTPS + client-cert mTLS in front of `/v1/infer` |
| Artifact Registry | `akshrava-api` / `akshrava-worker` images |
| Secret Manager | DB URL, Redis URLs, HMAC secret, JWT keys, mTLS PEMs |
| GCS diagnostics bucket | Consented uploads only (`GCP_DIAGNOSTICS_BUCKET`) |

### Operator sequence

```bash
./scripts/build_gcp_images.sh "$PROJECT_ID" us-central1
# Copy the printed api_image / worker_image digests into gcp/terraform.tfvars
cp gcp/terraform.tfvars.example gcp/terraform.tfvars   # set project_id + digests
# Optional remote state + CMEK: copy gcp/backend.tf.example → gcp/backend.tf
terraform -chdir=gcp init && ./scripts/gcp_migrate_then_deploy.sh "$PROJECT_ID" us-central1
terraform -chdir=gcp output websocket_url
```

`gcp_migrate_then_deploy.sh` runs `terraform apply` then `gcloud run jobs execute akshrava-migrate
--wait` so Alembic must succeed before you treat the new API revision as live.

Cloud Run defaults to **authenticated invokers only** (`api_allow_unauthenticated=false`).
`terraform plan` and `scripts/gcp_preflight.sh` **fail** if both `api_invoker_members` is empty and
`api_allow_unauthenticated` is false — phones cannot reach private Cloud Run without an edge.
Grant `api_invoker_members` for an edge proxy SA/group, or set `api_allow_unauthenticated=true`
only for a temporary documented public pilot. `/healthz` and `/readyz` remain usable for probes;
`/metrics` requires `METRICS_SCRAPE_TOKEN` (Secret Manager) via `X-Akshrava-Metrics-Token` or
`Authorization: Bearer`.

PKI: `manage_pki_in_terraform` defaults to **false**. Supply external PEM variables (see
`terraform.tfvars.example`). Bootstrap-only may set `manage_pki_in_terraform=true` (keys land in
state — rotate if state is copied).

WSS reliability: the API service uses `cpu_idle=false` and `min_instance_count=1`.

Redis: AUTH is always on. Set `redis_transit_encryption=true` to move to Memorystore STANDARD_HA
with `rediss://` URLs when in-transit TLS is required.
Default `detector = "noop"` brings up API + SQL + Redis so Android can complete an authenticated
session without GPU weights. Switch to `detector = "remote"` only with a 64-char
`yolo_weights_sha256` and weights installed on the worker model volume.

Fetch the provisioning private key (never mount it on Cloud Run):

```bash
gcloud secrets versions access latest --secret=akshrava-jwt-private > jwt-private.pem
```

Point the Android app at the `websocket_url` output (`wss://…/v1/session`). Release builds still
require WSS only.
