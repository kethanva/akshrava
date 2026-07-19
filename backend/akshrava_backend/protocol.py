from typing import Any, Dict

from .domain import FrameHeader


class ProtocolError(ValueError):
    pass


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
    )


def quality_message(max_side=640, jpeg_q=60, fps=1.0):
    return {"type": "quality", "max_side": max_side, "jpeg_q": jpeg_q, "fps": fps}


def quality_for_inference(inference_ms: int):
    """Bound capture cost when server work consumes the freshness budget.

    Network staleness remains phone-owned. This response only reacts to server queue/inference
    time and stays within the app's supported quality range.
    """
    if inference_ms > 350:
        return quality_message(max_side=384, jpeg_q=35, fps=0.5)
    if inference_ms > 180:
        return quality_message(max_side=512, jpeg_q=45, fps=0.7)
    return quality_message()
