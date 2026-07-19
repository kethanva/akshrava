# Privacy programme — Akshrava assistive vision pilot

This document implements the minimum viable privacy programme described in the build plan
(Part 10). It is practical engineering guidance, not legal advice. Obtain Indian privacy
counsel before any public deployment.

## Legal context

The [DPDP Act, 2023](https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023.pdf)
and [DPDP Rules, 2025](https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa?pageTitle=Digit)
were notified with phased commencement. The core processing/notice/consent and
Data Principal-rights sections are scheduled approximately 18 months after the
November 2025 notification — around May 2027. Build to that standard now.

---

## 1. Data map

| Data item | Purpose | Processor / location | Retention | Access owner | Deletion method |
|---|---|---|---|---|---|
| Phone number (hashed) | Device registration | Backend DB (India VM) | Until device deregistered | Project lead | DELETE from `devices` table |
| Device ID (random, rotating) | Session binding | Backend DB | Until device deregistered | Project lead | DELETE from `devices` table |
| Calibration ID | Camera geometry validation | Backend DB | Until recalibration | Project lead | UPDATE calibration record |
| Frame JPEG (640 px) | Live hazard detection | **RAM only** — discarded after inference | 0 seconds (ephemeral) | N/A | Automatic; never written to disk |
| Detection results | Alert generation | Backend RAM | Duration of WebSocket session | N/A | Garbage collected on session close |
| Alert events (kind, level, bearing, confidence) | Audit, regression analysis | Backend DB | 30 days | Project lead | Scheduled DELETE job |
| Coarse telemetry (inference latency, frame age, battery temp) | Service monitoring | Structured logs | 30 days | Project lead | Log rotation |
| Security audit events (auth failures, rate limits) | Incident detection | Structured logs | 90 days | Project lead | Log rotation |
| Opt-in diagnostic frames (consented, blurred) | Failure investigation, model improvement | Encrypted object storage | 30 days unless incident consent extends | Project lead | Auto-delete job; manual deletion on request |
| GPS / location | **Not collected in Phases 0–3** | N/A | N/A | N/A | N/A |

---

## 2. Default minimisation

- Process each frame **in RAM**; return detection results; **discard the frame immediately**.
- Do **not** write raw uploaded frames, GPS trails, audio, or continuous video in normal operation.
- Do not use IMEI — use a rotating random device ID.
- Do not perform facial recognition, demographic inference, or persistent bystander tracking.

---

## 3. Consent conversation

A volunteer reads the following notice **orally** in the participant's language before first use:

> **What the camera sees:** The phone camera captures images of the area in front of you,
> including bystanders in public spaces.
>
> **How images are processed:** Each image is sent to a server, analysed for obstacles,
> and immediately discarded. No images are saved by default.
>
> **Optional diagnostic samples:** If you agree separately, short clips from failed detections
> may be saved for 30 days to improve the system. Faces and number plates are blurred before
> storage. You can withdraw this consent at any time.
>
> **How to stop:** Press the Stop button, ask your guide, or remove the headset. The system
> stops capturing immediately.
>
> **How to withdraw or contact us:** [NGO contact email/phone]. You may request deletion of
> your device data at any time.

Record: consent version, date, language, volunteer name. Do **not** photograph a blind
person's signature as consent evidence.

---

## 4. Opt-in diagnostic samples

- Require **separate, revocable** voice-confirmed consent (distinct from operational consent).
- **Blur on phone before upload**: use detector-based face/plate blur plus manual review.
- Automated blur is imperfect — never promise perfect anonymisation.
- Retain for 30 days maximum, then auto-delete unless a specific incident consent extends.
- Do not upload diagnostic samples without active consent flag in device config.

---

## 5. Encryption and access

| Layer | Measure |
|---|---|
| Transit | TLS 1.3 / WSS only; no unencrypted endpoints |
| At rest | Encrypted disks/backups on all servers |
| Keys | Separate per-environment keys; rotate device tokens at re-provisioning |
| Access | Least-privilege service accounts; MFA for console access |
| Logs | No frames in logs; no PII in structured telemetry |
| Device tokens | Short-lived JWT (RS256 in production); re-authenticated on each WebSocket connection |

---

## 6. Retention and deletion schedule

| Data category | Retention | Deletion method |
|---|---|---|
| Operational telemetry | 30 days | Log rotation / scheduled DELETE |
| Security audit events | 90 days | Log rotation |
| Opt-in diagnostic clips | 30 days (unless incident consent extends) | Auto-delete job |
| Device records | Until device deregistered | On request |
| Alert events | 30 days | Scheduled DELETE job |

**User/device deletion request:** On request (oral or written), delete all records associated
with the device ID within 72 hours. Produce a deletion log entry (device ID hash, deletion
timestamp, operator).

---

## 7. Processor hygiene

- Sign data-processing terms with cloud hosts where possible.
- Know the country/region of all data processors.
- Do **not** put frames into free third-party demos, public issue trackers, or personal cloud storage.
- If a cloud host retains diagnostic data, document this in the data map.

---

## 8. Incident readiness

| Item | Implementation |
|---|---|
| Contact point | [NGO email/phone — fill before deployment] |
| Incident register | Spreadsheet or database: date, description, affected devices, actions taken, resolution |
| Access revocation | Revoke device token + close WebSocket; documented procedure |
| Breach decision tree | 1. Contain (revoke access) → 2. Assess (what data, how many) → 3. Notify (data principal if required by DPDP) → 4. Remediate → 5. Post-mortem |

---

## 9. Children

If a child (under 18) is participating:
- Obtain **verifiable parent/guardian consent**.
- Get specialist legal advice.
- The NGO is likely the data fiduciary even if a cloud host processes data.

---

## 10. Bystanders

The participant's consent does not obtain consent from bystanders captured in public.
The safest practical position:
- Ephemeral, no-retention processing of the minimum pixels needed for the service.
- No facial recognition.
- Separate legal review of the NGO's lawful basis for any retained clip.

---

## Review schedule

Review this document:
- Before each phase transition (Phase 0→1, 1→2, etc.)
- When adding any new data collection (especially GPS in Phase 4)
- When changing cloud providers or data processors
- After any security incident
