from typing import Any, Dict

from .domain import FrameHeader


class ProtocolError(ValueError):
    pass


# Server-rendered speech (spoken_preview, look_summary) must match the phone's own provisioned
# language (plan §6.2 — language is a per-device setting), not a fleet-wide server default.
# Allowlisted rather than free text: an unrecognised value silently falls back to English in
# composer.render() rather than ever being used to build a lookup key or format string.
SUPPORTED_LANGUAGES = {"en", "hi", "ta", "kn", "ml", "te"}


def _integer(payload: Dict[str, Any], key: str, minimum=0, required=True):
    value = payload.get(key)
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError("%s must be an integer >= %s" % (key, minimum))
    return value


def parse_frame_header(payload: Dict[str, Any]) -> FrameHeader:
    if payload.get("type") != "frame":
        raise ProtocolError("expected frame header")
    width = _integer(payload, "w", 1)
    height = _integer(payload, "h", 1)
    return FrameHeader(
        frame_id=_integer(payload, "id", 0),
        capture_mono_ms=_integer(payload, "capture_mono_ms", 0),
        capture_epoch_ms=_integer(payload, "capture_epoch_ms", 0, required=False),
        width=width,
        height=height,
        jpeg_bytes=_integer(payload, "jpeg_bytes", 1),
        calibration_id=str(payload.get("camera_calibration_id", ""))[:128],
        pitch_cdeg=_integer(payload, "pitch_cdeg", -9000, required=False),
        roll_cdeg=_integer(payload, "roll_cdeg", -9000, required=False),
        pose_age_ms=_integer(payload, "pose_age_ms", 0, required=False),
        mode=str(payload.get("mode", "normal"))[:32],
        priority=bool(payload.get("priority", False))
        or str(payload.get("mode", "")) == "priority",
        trace_id=str(payload.get("trace_id", ""))[:64],
        language=str(payload.get("language", ""))[:2].lower(),
    )


def quality_message(max_side=640, jpeg_q=55, fps=1.0):
    return {"type": "quality", "max_side": max_side, "jpeg_q": jpeg_q, "fps": fps}


def quality_for_inference(inference_ms: int):
    """Bound capture cost when server work consumes the freshness budget.

    Tuned for CPU remote YOLO (multi-second capable) and constrained 3G/4G uplinks: shed
    resolution/JPEG quality/FPS early so more frames finish inside the phone freshness window.
    Network staleness remains phone-owned. This response only reacts to server queue/inference
    time and stays within the app's supported quality range.

    Ladder:
      normal → 640/Q55/1.0
      >150ms → 512/Q48/0.85
      >280ms → 480/Q42/0.7
      >600ms → 384/Q35/0.55
      >1200ms → 320/Q32/0.45
      >2500ms → 320/Q28/0.35
    """
    if inference_ms > 2500:
        return quality_message(max_side=320, jpeg_q=28, fps=0.35)
    if inference_ms > 1200:
        return quality_message(max_side=320, jpeg_q=32, fps=0.45)
    if inference_ms > 600:
        return quality_message(max_side=384, jpeg_q=35, fps=0.55)
    if inference_ms > 280:
        return quality_message(max_side=480, jpeg_q=42, fps=0.7)
    if inference_ms > 150:
        return quality_message(max_side=512, jpeg_q=48, fps=0.85)
    return quality_message()
