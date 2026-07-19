# Field readiness and supervised trial guide

This system is a bench/supervised-pilot implementation until every applicable item below is signed
off by a named release owner. Passing code tests never authorizes independent street use.

## Phone qualification and provisioning

- Battery health exceeds 80% original capacity; a continuous-camera stress test has no abrupt
  shutdown, thermal excursion above 45°C, or unsafe swelling/heat.
- Rear camera is clear; Tier-A phones have Android 10+, 64-bit ARM, 4 GB RAM, and 32 GB storage.
- Factory reset, install available security updates, remove nonessential bloatware, install the
  signed APK, and grant Camera, notification, and battery-optimization permissions.
- Install an active SIM/data plan, provision a unique device token and calibration ID, and verify
  the visible Start/Stop controls, notification, WSS connection, outage phrase, and no fake local
  fallback.
- Verify worn-mount orientation, offline Hindi/English TTS, heat/battery, lock-screen survival,
  haptics, and carrier freshness. Reject damaged, 32-bit, 2 GB, or unreliable devices.

## Engineering and model gate

- Backend tests, Android tests, signed release artifact, deployment preflight, backups/restore,
  readyz recovery, retention, and protected monitoring access all pass.
- Production uses WSS, `DEV_AUTH_BYPASS=false`, RS256 device-token verification, per-device
  revocation, and an approved secrets-rotation procedure.
- The detector licence, exact weight SHA-256, latency evidence, model rollback image, verified
  calibration profile, controlled-course evidence, and private GPU-worker checks are recorded.
- `noop` is transport-only bench mode and must never be represented as vision assistance. An
  offline model remains disabled until its own recall, latency, heat, tensor-contract, and
  controlled-course evidence is approved.

## Supervised-trial protocol

Brief participants that Akshrava is experimental and never replaces a cane, guide dog, or mobility
instructor. Demonstrate Start, Stop, alert vocabulary, and the lack of crossing/clear-path advice.
The supervisor stays beside or behind the participant, intervenes immediately for danger, and is
the authority for street crossings. Use a controlled enclosed course first, then a quiet familiar
route; never promise offline detection without an explicitly approved local model.

Record alert reactions, misses/false positives, battery/thermal behavior, and oral feedback. Stop
the session after an unexpected urgent miss, repeated stale alert, unannounced service death,
overheating, fall/near fall, or participant distress. Every incident reopens the relevant release
gate before another session.

## Explicit external gates

Licensed/evaluated weights, a named NGO or mobility instructor, accessible consent, privacy/legal
review, controlled-course evidence, and a real field deployment are not artifacts that can be
created in this repository. They remain required before supervised or public use.
