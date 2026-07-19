# Release and verification guide

## Engineering release

1. Work on a `codex/` release branch; keep backend tests/lint, Compose configuration, and Android
   builds green.
2. Open a reviewed pull request to `main`; merge only after required CI passes.
3. Bump backend and Android versions, create an annotated `vX.Y.Z` tag on the merged commit, and
   publish only the workflow-produced signed artifacts.
4. Record the commit, tag, schema revision, model/weight SHA-256, and deployment configuration in
   the release log. Roll back to the last approved tag only after stopping affected sessions.

## Repository verification

```bash
./scripts/verify_phases.sh
./scripts/test_backend.sh
cd backend && .venv/bin/ruff check akshrava_backend tests
cd android && ./gradlew --no-daemon :app:testDebugUnitTest :app:assembleDebug :app:assembleRelease
```

The current backend suite verifies protocol, freshness, priority look, geometry gating, storage,
distributed coordination, migration, remote-worker, authentication, and fail-closed contracts.
It does not prove street perception, mount calibration, CUDA performance, carrier handover, or
field safety.

Before a deployment, run `cloud_preflight.sh .env` (and `--field` / `--gpu-worker` where relevant),
rehearse Alembic upgrade/rollback against a restored backup, and validate readiness, metrics, mTLS,
worker failover, and device revocation. Complete every item in [FIELD_GUIDE.md](FIELD_GUIDE.md)
before treating a GitHub release as a field release.

## Verification boundary

Bench/CI proves code behavior. Supervised participant and independent field use remain blocked
until the model, device, instructor, consent, and controlled-course evidence described in the field
guide is signed off.
