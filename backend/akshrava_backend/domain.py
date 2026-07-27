from dataclasses import dataclass, field


@dataclass(frozen=True)
class FrameHeader:
    frame_id: int
    capture_mono_ms: int
    capture_epoch_ms: int | None
    width: int
    height: int
    jpeg_bytes: int
    calibration_id: str
    pitch_cdeg: int | None
    roll_cdeg: int | None
    pose_age_ms: int | None
    mode: str
    priority: bool = False
    trace_id: str = ""
    language: str = ""
    debug_telemetry: bool = False


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: tuple[float, float, float, float]


@dataclass(frozen=True)
class GeometryProfile:
    """A verified, versioned mount/camera profile for one calibration ID.

    `focal_px` is defined at `reference_height_px` (default 480 for the 640×480 JPEG baseline).
    Hazard scoring scales focal to the current frame height so quality downscaling does not
    inflate range estimates.
    """

    calibration_id: str
    focal_px: float
    camera_height_m: float
    reference_height_px: int = 480


@dataclass
class Track:
    track_id: int
    label: str
    confidence: float
    box: tuple[float, float, float, float]
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
    track_id: int | None = None


@dataclass
class SessionState:
    device_id: str
    # Connection-scoped key for tracker lifetime. Must not be bare device_id: an old socket's
    # teardown must not wipe tracker state belonging to a newer reconnect of the same phone.
    session_key: str = ""
    trace_prefix: str = ""
    calibration_id: str = ""
    tracks: list[Track] = field(default_factory=list)
    last_alert_at_ms: dict[str, int] = field(default_factory=dict)
    alert_timestamps_ms: list[int] = field(default_factory=list)
    last_capture_mono_ms: int | None = None
    last_pitch_cdeg: int | None = None
    last_roll_cdeg: int | None = None
    geometry_profile: GeometryProfile | None = None
    language: str = ""
    diagnostic_consent: bool = False
