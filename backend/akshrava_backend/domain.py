from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class FrameHeader:
    frame_id: int
    capture_mono_ms: int
    capture_epoch_ms: Optional[int]
    width: int
    height: int
    jpeg_bytes: int
    calibration_id: str
    pitch_cdeg: Optional[int]
    roll_cdeg: Optional[int]
    pose_age_ms: Optional[int]
    mode: str
    priority: bool = False
    trace_id: str = ""


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: Tuple[float, float, float, float]


@dataclass(frozen=True)
class GeometryProfile:
    """A verified, versioned mount/camera profile for one calibration ID."""

    calibration_id: str
    focal_px: float
    camera_height_m: float


@dataclass
class Track:
    track_id: int
    label: str
    confidence: float
    box: Tuple[float, float, float, float]
    hits: int = 1
    missed: int = 0


@dataclass(frozen=True)
class Hazard:
    kind: str
    level: str
    bearing: str
    message_key: str
    haptic: str
    confidence: float
    severity: str = "S2"
    range_band: str = "unknown"
    range_valid: bool = False
    track_id: Optional[int] = None


@dataclass
class SessionState:
    device_id: str
    trace_prefix: str = ""
    calibration_id: str = ""
    tracks: List[Track] = field(default_factory=list)
    last_alert_at_ms: Dict[str, int] = field(default_factory=dict)
    alert_timestamps_ms: List[int] = field(default_factory=list)
    last_capture_mono_ms: Optional[int] = None
    last_pitch_cdeg: Optional[int] = None
    last_roll_cdeg: Optional[int] = None
    geometry_profile: Optional[GeometryProfile] = None
