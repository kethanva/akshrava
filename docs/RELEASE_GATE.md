# Release gate

This repository is a bench-test implementation until every relevant item below is signed off. Passing code tests does **not** authorise independent street use.

## Code and deployment

- [ ] `./scripts/test_backend.sh` passes and the Android debug APK is assembled from the exact release commit.
- [ ] A release build connects only to a real `wss://` endpoint; `DEV_AUTH_BYPASS=false`; a long random `JWT_SECRET` is stored outside Git.
- [ ] `AKSHRAVA_ENV` is `pilot` or `production`; geometry output remains disabled unless the
      device has a verified calibration profile and controlled-course evidence.
- [ ] `cloud_preflight.sh .env --field` passes; HTTPS/WSS DNS, Caddy certificate renewal,
  firewalling of port 8000, database backup/restore, token expiry, per-device revocation and
  emergency JWT-secret rotation are exercised.
- [ ] `/readyz` remains healthy through a database restart/recovery drill, alert-event retention
  is observed, and monitoring/admin ports are reachable only through protected operator access.
- [ ] The approved detector package, exact weights SHA-256, licence decision, latency measurement, and rollback image are recorded. `noop` is never represented as vision assistance.
- [ ] For split deployment, `cloud_preflight.sh .env --gpu-worker` passes on the GPU host; the CUDA worker `/readyz` is healthy, its private metrics target is scraped, and the control-plane-to-worker HMAC/replay rejection test is exercised over the approved private network.
- [ ] Device event reads are tested with the device's own token and rejected with another device's token.

## Phone provisioning and field gate

- [ ] Exact phone model, Android build, camera, mount orientation, offline Hindi/English speech, haptics, battery condition, heat, lock-screen survival, and two carriers pass the approved-device checklist.
- [ ] A sighted volunteer verifies the outage phrase, stale-result rejection, Stop control, notification permission, and that no background camera restart occurs after an OEM kill.
- [ ] The local/offline detector remains disabled unless its separate target-device recall, latency, heat, and controlled-course evidence meets its narrow declared policy.
- [ ] Consent, incident process, mobility-instructor supervision, and cane/guide requirement are in place for every field session.

## Deferred for now (disabled)

- **DPDP formal legal sign-off** — the published Hindi/English privacy notice and the one-hour
  Indian data-privacy lawyer review are **deferred** and do not block bench progress. This only
  removes the paperwork/legal-review as a gate item; the code-level protections stay in force
  (frames are never persisted, alert-event retention runs, tokens are per-device, no face
  recognition) and per-session **spoken consent + mobility-instructor supervision + cane/guide**
  remain hard field-gate requirements above. Re-enable before any non-supervised or public
  deployment.

Only a named release owner should mark these complete. Any alert miss, unexpected alert, or service death ends the session and reopens the relevant gate.
