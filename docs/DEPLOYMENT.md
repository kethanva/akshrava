# Deployment guide

This repository supports a single-host deployment and a split deployment. In the split form, the
**control plane** owns phone WebSockets, authentication, alert scoring and PostgreSQL; the
**GPU worker** accepts only a short-lived HMAC-authenticated JPEG from that control plane and
returns detector boxes. It has no database, phone endpoint, device token or alert history.

## Before deployment

- Keep `DETECTOR=noop` until the model licence, exact model file/hash, target-device benchmark,
  labelled evaluation and controlled-course gate are approved.
- Set unique, high-entropy `JWT_SECRET` and `POSTGRES_PASSWORD` values. Never use the examples.
- Set `AKSHRAVA_ENV=pilot` or `production`; the service rejects `DEV_AUTH_BYPASS=true` outside
  an explicit `development` environment.
- Provision a domain and DNS before enabling Caddy. The public phone endpoint must be WSS.
- Do not deploy a phone without its calibration/provisioning and supervised-trial sign-offs.

## Control-plane deployment

```bash
cd infra
cp .env.example .env
# Set POSTGRES_PASSWORD, JWT_SECRET and DOMAIN in .env.
../scripts/cloud_preflight.sh .env
docker compose --env-file .env --profile control-plane --profile edge up -d --build
```

The API is deliberately bound to `127.0.0.1:8000`; Caddy is the public TLS/WSS endpoint. Open
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
URLs outside development. Set the same `REMOTE_WORKER_SECRET` on both
hosts, set the control plane to `DETECTOR=remote`, and point `REMOTE_INFERENCE_URL` at the worker
`/v1/infer` endpoint. The control plane timeout defaults to 450 ms; a failed or late worker result
causes the existing fail-closed phone messaging.

On the GPU host, use the same approved model mount and only the GPU profile:

```bash
cd infra
../scripts/cloud_preflight.sh .env --gpu-worker
GPU_WORKER_BIND_ADDRESS=10.0.0.12 docker compose --env-file .env --profile gpu-worker up -d --build
```

`api-gpu` uses a CUDA PyTorch runtime and one Gunicorn worker so a single GPU model is not
replicated accidentally. It refuses to start if CUDA is unavailable. It has a health endpoint at
`/readyz` and a private metrics endpoint. Add its private endpoint to
`infra/prometheus-targets/gpu-workers.json` on the monitoring host, then reload Prometheus. The
worker uses HMAC request authentication with timestamp replay protection; network isolation and
TLS/mTLS remain mandatory deployment controls.

## Model activation

Only after the release gate is met, set `INSTALL_YOLO=true`, mount an approved model file via
`MODEL_DIR`, set `DETECTOR=ultralytics` (single-host) or `DETECTOR=remote` (split deployment),
and record the file SHA-256 in the deployment record.
`YOLO_WEIGHTS` must resolve to that read-only mounted file. The server must never download weights
while serving a session.

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

The current pilot startup supports only the tiny, reviewed additive SQLite compatibility upgrades
that are tested in `backend/tests/test_storage_migrations.py`; names and SQL types are fixed
allow-lists, never operator input. Before any production PostgreSQL schema change or non-additive
upgrade, replace that pilot bridge with an Alembic migration revision, rehearse upgrade and rollback
against a restored backup, and record the revision in the release log. Do not add ad-hoc runtime
DDL for future schema changes.

There is no enabled Android fallback. If the network, control plane, or GPU worker fails, the app
must say vision assistance is unavailable and the user must continue with a cane or guide.

## Calibration profile activation

The backend records calibration IDs on devices, but it loads geometry only from a separately
verified `calibration_profiles` record (focal length and mounted camera height). Unknown or
unverified profiles keep `range_valid=false`. Provisioning must create, verify and audit that
record only after the device/mount controlled-course gate; do not treat a string calibration ID as
measurement evidence.

## Scale and failover boundary

One configured remote worker endpoint is adequate for the supervised pilot. The worker's HMAC nonce
replay cache is intentionally process-local and the image starts one Gunicorn worker; do not put
multiple replicas behind a load balancer. Before multi-worker or interruptible-GPU operation, add
an authenticated endpoint registry, health-checked re-pointing, a shared atomic nonce store (for
example Redis with TTL), and a warm-spare drill. Do not claim automatic GPU failover from this
Compose deployment alone.
