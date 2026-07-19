# Phone ↔ backend protocol

The phone opens exactly one `wss://HOST/v1/session` socket while assistance is explicitly active and sends `Authorization: Bearer JWT` in the WebSocket upgrade request. The backend accepts no unauthenticated production connection. The client keeps **one** frame in flight and may discard its one replaceable pending frame.

## Messages

1. Server → client: `{"type":"ready","device_id":"...","max_in_flight":1,"vision_enabled":true}`. A `false` value means the server is in transport-only bench mode; the phone must say vision is unavailable and must not send frames.
2. Client → server: a JSON `frame` header followed immediately by one binary JPEG message.
3. Server → client: a `result`, followed by optional `quality` guidance.
4. Optional client → server: `{"type":"look"}` then a priority frame (`priority: true` and/or `mode: "priority"`). The server replies `look_ack`, then the priority result includes `look_summary` and **skips alert cooldowns / device rate limits** so the explicit query is answered for the current frame. On the phone, look answers use the **500 ms** freshness budget (not the tighter 250 ms S1 window).

```json
{
  "type": "frame",
  "id": 841,
  "capture_mono_ms": 93211455,
  "capture_epoch_ms": 1752883094000,
  "w": 640,
  "h": 480,
  "jpeg_bytes": 61423,
  "camera_calibration_id": "pilot-phone-r0",
  "pitch_cdeg": -1180,
  "roll_cdeg": 90,
  "pose_age_ms": 12,
  "mode": "normal",
  "priority": false
}
```

`capture_mono_ms` is a **phone-local** elapsed-realtime timestamp. The backend echoes it but does not compare it with its own clock. The phone discards a normal alert when `elapsedRealtime() - capture_mono_ms > 500` (urgent S1: 250 ms). Priority look results use **500 ms** even when the hazard is urgent.

Pose values are calibration/validity signals. Missing or unverified geometry keeps `range_valid=false`. A separately verified `calibration_profiles` record (focal length + mounted camera height) plus fresh pose and dual-estimate agreement may set `range_valid=true`. The calibration ID string alone is never measurement evidence. Never manufacture a spoken distance from pose alone.

`quality` is bounded server-pressure guidance. At normal load it requests 640 px/Q60/1 FPS; when server queue/inference consumes the freshness budget it requests 512 px/Q45/0.7 FPS, then 384 px/Q35/0.5 FPS. The phone remains responsible for discarding stale network results.

The server also enforces a per-session normal-rate token bucket of 1.2 frames/second with a two-frame burst. Priority look frames bypass that bucket. Non-priority excess headers are rejected as `frame_rate_limited` and their paired JPEGs are consumed so the stream stays synchronised.

## Safety invariants enforced in code

- Images are size limited (default 200 KB) and never persisted.
- Capture timestamps must strictly increase and the server refuses non-priority frame headers closer than 200 ms by default; rejected header/JPEG pairs are consumed together so the socket remains synchronised.
- A vehicle message key is `vehicle_nearby`, never an approach/crossing instruction.
- S2/caution alerts require two detections on a track; single-frame S1 is allowed only when geometry is `range_valid` and confidence clears the S1 threshold.
- When a hazard is returned, the backend sets `motion_evidence: "insufficient"` at this frame rate.
- The phone owns staleness rejection and TTS rate limiting.
- Priority look (`priority` / `mode=priority`) sets `skip_cooldowns` server-side and returns `look_summary`; it does not invent approach or safe-to-cross language.
