# Phone ↔ backend protocol

The phone opens exactly one `wss://HOST/v1/session` socket while assistance is explicitly active and sends `Authorization: Bearer JWT` in the WebSocket upgrade request. The backend accepts no unauthenticated production connection. The client keeps **one** frame in flight and may discard its one replaceable pending frame.

## Messages

1. Server → client: `{"type":"ready","device_id":"...","max_in_flight":1,"vision_enabled":true}`. A `false` value means the server is in transport-only bench mode; the phone must say vision is unavailable and must not send frames.
2. Client → server: a JSON `frame` header followed immediately by one binary JPEG message.
3. Server → client: a `result`, followed by optional `quality` guidance.

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
  "mode": "normal"
}
```

`capture_mono_ms` is a **phone-local** elapsed-realtime timestamp. The backend echoes it but does not compare it with its own clock. The phone discards an alert result when `elapsedRealtime() - capture_mono_ms > 500`. This prevents clock drift from becoming a stale-alert bug.

Pose values are calibration/validity signals only. The current implementation always emits `range_valid=false`: the calibration ID is recorded, but verified intrinsics, mount height and a ground-plane transform are not yet implemented. It must never manufacture a spoken distance or urgency from pose alone.

`quality` is bounded server-pressure guidance. At normal load it requests 640 px/Q60/1 FPS; when server queue/inference consumes the freshness budget it requests 512 px/Q45/0.7 FPS, then 384 px/Q35/0.5 FPS. The phone remains responsible for discarding stale network results.

The server also enforces a per-session normal-rate token bucket of 1.2 frames/second with a two-frame burst. It rejects excess headers as `frame_rate_limited` and consumes their paired JPEGs so the stream stays synchronised.

## Safety invariants enforced in code

- Images are size limited (default 200 KB) and never persisted.
- Capture timestamps must strictly increase and the server refuses frame headers closer than 200 ms by default; rejected header/JPEG pairs are consumed together so the socket remains synchronised.
- A vehicle message key is `vehicle_nearby`, never an approach/crossing instruction.
- A track must be detected twice before it can generate an alert.
- The backend returns `motion_evidence: "insufficient"` at this frame rate.
- The phone owns staleness rejection and TTS rate limiting.
