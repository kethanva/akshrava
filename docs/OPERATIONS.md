# Operator runbook

## Before any field use

1. Keep `DETECTOR=noop` until model licensing, target-device benchmarks, a labelled evaluation set, and the controlled-course gate are complete. The default server is intentionally a transport/policy integration test, not a vision product.
2. Create a unique device ID and a short-lived token. Do not use `dev-device-token` outside local development.
3. Provision only a Tier-A device: camera, mount, offline TTS, locked-screen service survival, battery/heat and carrier freshness must pass.
4. Configure `wss://your-domain/v1/session`, token and calibration ID while the phone UI is visible. The user must press Start assistance; Android does not permit a silent background camera start.
5. Use a cane/guide and a named mobility instructor for every field session. No independent street use.
6. After controlled-course mount verification, upsert geometry so `range_valid` can leave fail-closed:

```bash
export DATABASE_URL='postgresql+asyncpg://…'
# From repo root with backend deps available:
PYTHONPATH=backend python scripts/upsert_calibration_profile.py pilot-phone-r0 \
  --focal-px 520 --camera-height-m 1.35 --confirm-verified
```

Without `--confirm-verified` the row stays fail-closed (`geometry_profile` returns null).

## Local backend

```bash
./scripts/test_backend.sh
./scripts/run_backend_dev.sh
curl http://127.0.0.1:8000/healthz
```

For an Android emulator/debug device only, configure `ws://10.0.2.2:8000/v1/session` with token `dev-device-token`. Debug builds allow cleartext only for `10.0.2.2`, `127.0.0.1`, or `localhost`; release builds reject it. The production endpoint must be WSS.

## Pilot deployment

```bash
cd infra
cp .env.example .env
# Edit passwords, RS256 public-key path, and DOMAIN. Do not use example values.
../scripts/cloud_preflight.sh .env
docker compose --env-file .env --profile control-plane --profile edge up -d --build
```

Use URL-safe secret values for `POSTGRES_PASSWORD` because Compose places it in the database URL; for example, `openssl rand -hex 32`. The API port is intentionally bound only to `127.0.0.1`; Caddy is the public WSS endpoint.

Caddy obtains and renews TLS for `DOMAIN`; expose TCP 80/443 to the host and point its DNS A/AAAA record at the server first. The raw API port 8000 is a development port—firewall it in a public deployment.

Use `/readyz`, not `/healthz`, for deployment readiness: it verifies the database connection.
In production it also verifies the Redis-backed coordination controls used for session admission
and frame limiting.
Take a backup and test restoring it before each release:

```bash
../scripts/backup_postgres.sh .env /secure/backup/directory
```

Mint a production device token from the secured provisioning workstation; its private key is not
mounted on the API host:

```bash
cd backend
source .venv/bin/activate
JWT_ALGORITHM=RS256 JWT_PRIVATE_KEY_FILE=/secure/keys/device-private.pem \
  python ../scripts/mint_device_token.py pilot-phone-001 --days 30
```

The Android app encrypts this token using the device's Android Keystore before storing it. A
keystore failure means the volunteer must re-provision; it must not fall back to plaintext storage.

To revoke a lost or compromised provisioned device, run from an operator machine with
the production `DATABASE_URL`:

```bash
cd backend
DATABASE_URL='postgresql+asyncpg://...' python ../scripts/revoke_device.py pilot-phone-001
```

Revocation is checked during WebSocket upgrade, before every frame header, and during event reads.
An already-open session is closed before its next frame; issue a new device ID/token after
re-provisioning, and do not "unrevoke" a lost device.

## Operational signals

Prometheus must alert on control-plane availability, GPU-worker availability, late-suppressed
results, session-admission saturation, and aggregate capture-to-API frame age. These metrics are
intentionally aggregate: no device, route, or carrier labels are exported from `/metrics`.
Outside development, `/metrics` requires `METRICS_SCRAPE_TOKEN` (`X-Akshrava-Metrics-Token` or
Bearer). Caddy and Cloud Run must not expose an unauthenticated scrape path publicly.
Carrier/device p95 is a field-log analysis task after explicit consent, not a public metrics label.

## Model activation

After the licence decision and model validation, set `INSTALL_YOLO=true`, mount the approved local model directory through `MODEL_DIR`, record the weight SHA-256, set `YOLO_WEIGHTS_SHA256` to that digest, and set `DETECTOR=ultralytics` for a single host or `DETECTOR=remote` for the control plane of a split deployment. Use `REMOTE_INFERENCE_REGISTRY_JSON` for multiple GPU workers so each device is placed stably and can fail through to a warm peer. `YOLO_WEIGHTS` must point at that read-only `/models/...` file on the inference host; the server rejects a missing path or mismatched digest and never downloads weights during a session. A model deployment must run the regression suite and controlled-course release gate before it reaches a phone.

The APK contains no Android TFLite fallback. Do not add an arbitrary `best_11n.tflite` file: a
future fallback needs an exact model asset, labels, tensor layout, preprocessing/postprocessing
contract, target-device benchmark and controlled-course evidence as one reviewed change.

## Failure handling

- **Connection lost:** the app says “Vision assistance unavailable. Use cane or guide.” It reconnects with backoff, but must not queue frames, claim a clear path, or imply a local fallback unless a separately evaluated model is explicitly enabled.
- **Service killed:** do not attempt silent restart. The user/volunteer must visibly start camera assistance again; investigate that model/OEM before reissuing it.
- **Hot/battery low:** stop the session or reduce capture according to the approved device policy. Do not charge a hot or swollen donated phone.
- **Unexpected alert/miss/near miss:** end the session, record the incident without raw video by default, and add consented diagnostic evidence to the regression process before the next trial.
