# CODEX.md

Akshrava was built as a safety-first assistive vision system for supervised use by blind and low-vision people, with a deliberate focus on recycled Android phones, conservative alerts, and cloud-assisted inference. This document is a short build history of how the project came together with Codex, what was learned along the way, and how the implementation was tested and debugged end to end.

## How the project was created

The project started from architecture and safety reviews rather than from a blank app. The early work defined the core boundary first: Akshrava must help with object and vehicle awareness, but it must not claim navigation, collision avoidance, crossing decisions, or “safe” guarantees.

From there, the implementation grew in layers:

1. The Android app was built as the user-facing capture and alert layer.
2. The FastAPI backend was built as the authenticated control plane.
3. GCP infrastructure was added for deployment, storage, and remote worker execution.
4. Tests, scripts, and observability were added so the system could be exercised repeatedly without guessing.
5. The docs were consolidated so the project could be understood and operated as one system instead of many disconnected parts.

## Build timeline from the repo history

The git log shows the project evolving in a clear sequence:

- early documentation work established the end-to-end architecture and safety boundary;
- the Android client was then hardened around capture, session state, alerts, icons, language support, and provisioning;
- the backend was aligned with conservative detection, calibration, and session admission rules;
- GCP and deployment scripts were added to make the cloud path real instead of theoretical;
- later commits focused on reconnect stability, freshness budgets, session longevity, and release/debug workflows;
- the final passes tightened docs and operational scripts so the same system could be reproduced on a physical phone.

The saved debug/session artifacts reinforced that history. They captured the real failure modes we had to fix: loopback endpoint mistakes, reconnect storms, stale-frame drops, and the difference between “connected” and “actually ready to stream detections.”

## How Codex helped

Codex was used as a coding and review partner throughout the project. Codex helped with:

- tracing bugs from Android capture through WebSocket transport to backend inference;
- turning architecture-review comments into concrete code fixes;
- identifying fail-open paths, replay risks, freshness bugs, and reconnect loops;
- drafting and refining tests before changes were merged;
- improving the documentation so the architecture, deployment, and runtime behavior matched the actual code;
- checking that the app, backend, and scripts all lined up as one end-to-end system.

Human review still handled the important decisions: approving safety trade-offs, supervising device testing, protecting credentials, and deciding when evidence was good enough to proceed.

## What we learned

The biggest lesson was that assistive systems are only as good as their weakest link. A healthy camera, a healthy backend, and a healthy model are not enough if the transport, provisioning, freshness gates, or reconnect logic are wrong.

We also learned that:

- a “working build” is not the same as a working real-world path;
- mobile and cloud failures need explicit recovery behavior, not silent retries;
- safety constraints have to be enforced in code, tests, and docs together;
- observability matters because the hardest bugs are often the ones that look like user error;
- recycled hardware can be useful, but only when setup, provisioning, and release steps are repeatable.

## How it was built

The implementation was developed in a few major passes:

- Android capture, session state, and alerting were hardened first so the phone could run as a stable front end.
- Backend session admission, detection, hazard policy, and storage were aligned with the Android protocol.
- Cloud Run, Redis, Cloud SQL, remote worker paths, and deployment scripts were added for production-style operation.
- Regression tests were added for protocol handling, calibration behavior, freshness rules, and session stability.
- Debug tooling and install scripts were added so physical-device setup could be reproduced.

The codebase now includes explicit checks for:

- endpoint policy and local-vs-live WebSocket handling;
- freshness and stale-result suppression;
- fail-closed calibration and replay protection;
- worker saturation and transient inference failures;
- safe shutdown and reconnect behavior;
- observability through structured logs, metrics, and scripts.

## Testing and debugging

Testing was not treated as a final step. It was part of the build loop.

The project used:

- Android unit tests and protocol tests;
- backend tests for admission, session behavior, inference, and hazard logic;
- end-to-end scripts for phone-to-cloud connectivity;
- live-device debugging traces to identify failure loops;
- deployment preflight and provisioning scripts to catch environment issues before runtime.

One important debugging pattern was the use of actual session evidence, not just code inspection. Saved logs showed how frame capture, WebSocket readiness, and reconnect behavior interacted in real use, which made it possible to distinguish transport failures from inference failures and from client-side gating.

## Deployment and operations

Deployment was built around a clear split:

- the Android app handles camera capture, local policy, and spoken alerts;
- Cloud Run handles authenticated session admission and result orchestration;
- private infrastructure handles worker execution, replay protection, and persistent storage.

Operational scripts were added so setup could be repeated on real devices and so deployment checks could be run before a supervised session. Observability was kept visible with logs, metrics, and health checks rather than hidden behind a single “success” indicator.

## Summary

Codex helped turn this from a long architecture discussion into a functioning end-to-end system. The project progressed from design reviews to implementation, then into hardening, testing, live debugging, and deployment support. The end result is a more complete, safer, and more understandable Akshrava codebase that can be maintained and extended with less guesswork.
