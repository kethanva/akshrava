# Deferred features — do not build these yet

This file exists because "a guardrail against attractive sensor-fusion ideas consuming
the time needed to make the base device reliable" (build plan §5).

Review this table before accepting a feature request, a PR, or a weekend tangent.
Move an item out only after the stated preconditions are met and the prior phase is stable.

| Proposal | Decision | Reason and earliest revisit |
|---|---|---|
| Always-on 1–3 FPS "reflex" detector for imminent collision | **Do not adopt** | Running the same low-rate detector all the time burns battery/heat but does not provide temporal resolution for a fast two-wheeler. No offline fallback is bundled; revisit only inside the separate 5–10+ FPS collision-research gate. |
| Optical-flow looming / time-to-contact | **Research only** | Promising for unknown obstacles, but lanyard bounce and wearer motion create global flow. It needs high-rate frames, ego-motion compensation and controlled ground truth before it can speak. Not a Phase 3 "quick win." |
| Local mini-tracker that says "approaching" | **Do not adopt** | A few low-rate box positions cannot disentangle wearer motion, turn, autofocus and target motion. It would reintroduce the forbidden approach claim. |
| GPS memory for confirmed static hazards | **Phase 4 option** | Potentially useful on repeat routes, but 5–15 m urban GPS error means caution zones only. Require separate opt-in for location, keep hazard points not trajectories, expiry/review workflow, and never phrase it as step-precise. |
| Foveated native-resolution ground crop | **Phase 4 experiment** | May help small pothole/drain recall. It adds a second image, server work and latency; test against a strict extra-byte/age budget rather than calling it bandwidth-neutral. Ship only if held-out recall improves without breaking freshness. |
| Vehicle time-to-collision alert | **Separate funded experiment** | Requires ≥5–10 FPS analysis (preferably local), IMU/visual ego-motion compensation, ground-truth method, day/night/weather evaluation, blind-mobility specialist review, and pre-agreed FP/FN thresholds. Until all gates pass, retain only "Vehicle nearby" as contextual information. |
| OCR / sign reading | **Phase 4+** | Street sign text is too small/blurred at 640 px. Add PaddleOCR only for user-requested single still-image "read sign" mode, not continuous streaming. |
| iOS port | **Phase 4+** | iOS 13 devices have tight background/camera behaviour and no value before Android is safe. Keep protocol portable but do not start Swift work. |
| Stereo panning audio | **Low-confidence enhancement** | Bluetooth latency/channel mappings vary and an earbud may be missing. A simple left/right haptic pattern is more reliable. |
| Depth Anything / monocular depth model | **Offline research only** | Adds latency and fails on glare, rain, uncalibrated cameras. Keep out of the alert path; use as an offline error-analysis / calibration cross-check only. |
