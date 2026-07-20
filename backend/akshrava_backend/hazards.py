import math
from typing import List, Optional, Tuple

from .domain import GeometryProfile, Hazard, SessionState, Track


VEHICLE_LABELS = {"car", "truck", "bus", "motorcycle", "bicycle", "auto_rickshaw"}
OBSTACLE_LABELS = {"person", "dog", "cat", "chair", "pole", "hawker_cart", "parked_vehicle"}

# S1 is the extreme-threat tier: high confidence, large + central, with a trusted range.
# It fires on a single frame (§5.3.5) because waiting a second frame at 1.5 FPS adds ~667 ms
# to the one alert that can least afford it. Everything else still needs two frames.
S1_CONFIDENCE = 0.6
MIN_CONFIDENCE = 0.45
S2_RISK_THRESHOLD = 0.4

# ---------- geometry constants ----------

# Known real-world heights for pinhole range estimation.
KNOWN_HEIGHTS_M = {
    "person": 1.65,
    "dog": 0.5,
    "car": 1.5,
    "truck": 1.5,
    "bus": 1.5,
    "auto_rickshaw": 1.5,
    "parked_vehicle": 1.5,
    "motorcycle": 1.4,
    "bicycle": 1.4,
    "pole": 3.0,
    "hawker_cart": 0.7,
    "chair": 0.7,
    "cat": 0.7,
}

# ---------- class weights for scoring formula ----------

CLASS_WEIGHT = {
    "person": 1.0,
    "dog": 1.0,
    "hawker_cart": 1.0,
    "cat": 1.0,
    "chair": 1.0,
    "parked_vehicle": 1.3,
    "pole": 1.3,
    "car": 1.2,
    "truck": 1.2,
    "bus": 1.2,
    "auto_rickshaw": 1.2,
    "motorcycle": 1.2,
    "bicycle": 1.2,
}

def _bearing(track: Track, width: int) -> str:
    center = (track.box[0] + track.box[2]) / 2.0
    ratio = center / max(width, 1)
    if ratio < 0.36:
        return "left"
    if ratio > 0.64:
        return "right"
    return "ahead"


def _effective_focal(profile: GeometryProfile, image_height: int) -> Optional[float]:
    """Scale calibrated focal_px from reference_height_px to the current JPEG height."""
    if image_height < 1 or profile.reference_height_px < 1:
        return None
    return profile.focal_px * (float(image_height) / float(profile.reference_height_px))


def _pinhole_distance(
    label: str,
    box: Tuple[float, float, float, float],
    image_height: int,
    profile: Optional[GeometryProfile],
) -> Optional[float]:
    """Calibrated pinhole estimate: distance = focal_px * H_known / box_height_px."""
    h_known = KNOWN_HEIGHTS_M.get(label)
    if h_known is None or profile is None:
        return None
    focal = _effective_focal(profile, image_height)
    if focal is None:
        return None
    box_height = max(0.0, box[3] - box[1])
    if box_height < 1.0:
        return None
    return focal * h_known / box_height


def _ground_plane_distance(
    box: Tuple[float, float, float, float],
    image_height: int,
    pitch_cdeg: Optional[int],
    profile: Optional[GeometryProfile],
) -> Optional[float]:
    """Ground-plane homography estimate from bottom edge of bounding box.

    Uses the camera pitch angle and the pixel offset of the box bottom from the
    optical centre to compute distance = H_camera / tan(pitch_angle + pixel_angle).
    """
    if pitch_cdeg is None or profile is None:
        return None
    focal = _effective_focal(profile, image_height)
    if focal is None:
        return None
    # Pitch in radians (centidegrees → degrees → radians).  Negative pitch means
    # the camera tilts downward, which is the normal walking orientation.
    pitch_rad = math.radians(pitch_cdeg / 100.0)

    # Pixel angle from optical centre (assumed at image_height / 2).
    cy = image_height / 2.0
    bottom_px = box[3]
    pixel_offset = bottom_px - cy
    if image_height < 1:
        return None
    pixel_angle = math.atan2(pixel_offset, focal)

    # Android pose uses negative pitch for a downward-mounted camera. Keep that sign: using
    # abs() turns an upward tilt into a downward one and can manufacture a near range.
    total_angle = -pitch_rad + pixel_angle
    if total_angle <= 0:
        return None
    distance = profile.camera_height_m / math.tan(total_angle)
    if distance <= 0:
        return None
    return distance


def _range_valid(
    pose_age_ms: Optional[int],
    pitch_cdeg: Optional[int],
    roll_cdeg: Optional[int],
    pinhole_dist: Optional[float],
    ground_dist: Optional[float],
    profile: Optional[GeometryProfile],
) -> bool:
    """Return false until each phone supplies a validated calibration profile.

    Generic focal-length, mount-height and object-height assumptions are research aids, not
    calibration data. Agreement between estimates built from the same assumptions is not enough
    to make a range claim or trigger a single-frame S1 prompt.
    """
    if profile is None:
        return False
    # Fresh pose required.
    if pose_age_ms is None or pose_age_ms >= 100:
        return False
    # Roll within ±12° (1200 centidegrees).
    if roll_cdeg is None or abs(roll_cdeg) > 1200:
        return False
    # Pitch must be present.
    if pitch_cdeg is None:
        return False
    # Both range estimates must be available.
    if pinhole_dist is None or ground_dist is None:
        return False
    # Estimates must agree within ±50%.
    if pinhole_dist <= 0 or ground_dist <= 0:
        return False
    ratio = pinhole_dist / ground_dist
    if ratio < 0.5 or ratio > 1.5:
        return False
    return True


def _range_band(distance: Optional[float]) -> str:
    """Map distance to a range band: near (<3m), ahead (3–6m), far (>6m)."""
    if distance is None:
        return "unknown"
    if distance < 3.0:
        return "near"
    if distance <= 6.0:
        return "ahead"
    return "far"


class HazardScorer:
    """Pure hazard ranking; intentionally no approach/crossing advice or state mutation."""

    def score(
        self,
        state: SessionState,
        width: int,
        height: int,
        pose_age_ms: Optional[int],
        pitch_cdeg: Optional[int],
        roll_cdeg: Optional[int],
        geometry_profile: Optional[GeometryProfile] = None,
        skip_cooldowns: bool = False,
    ) -> Optional[Hazard]:
        candidates: List[Hazard] = []
        for track in state.tracks:
            if track.confidence < MIN_CONFIDENCE:
                continue

            is_vehicle = track.label in VEHICLE_LABELS
            is_obstacle = track.label in OBSTACLE_LABELS
            if not (is_vehicle or is_obstacle):
                continue

            # ---------- geometry ----------
            pinhole_dist = _pinhole_distance(track.label, track.box, height, geometry_profile)
            ground_dist = _ground_plane_distance(track.box, height, pitch_cdeg, geometry_profile)
            
            valid_range = _range_valid(
                pose_age_ms, pitch_cdeg, roll_cdeg, pinhole_dist, ground_dist, geometry_profile
            )
            
            # Use pinhole as primary range estimate if available, else ground. `is not None`
            # matters here: a degenerate 0.0 estimate is falsy and would silently fall through
            # to ground_dist under a plain truthiness check.
            est_dist = pinhole_dist if pinhole_dist is not None else ground_dist
            band = _range_band(est_dist)
            
            bearing = _bearing(track, width)

            # ---------- scoring formula ----------
            cw = CLASS_WEIGHT.get(track.label, 1.0)
            proximity_validity = 1.0 if (valid_range and band == "near") else 0.6
            path_factor = 1.4 if bearing == "ahead" else 1.0
            # An on-demand look (skip_cooldowns) is, by design, exactly one pulled frame -- there
            # is no second observation coming. Requiring hits >= 2 here would make the feature
            # structurally unable to ever report a brand-new object, which defeats its purpose:
            # the plan's own justification for it is the standing-still blind spot, precisely
            # where ambient 0.2 FPS capture has NOT yet given the tracker a second look. Ordinary
            # ambient (non-priority) frames still require two-frame persistence to fire.
            stability = 1.0 if (skip_cooldowns or track.hits >= 2) else 0.0
            risk = cw * track.confidence * proximity_validity * path_factor * stability

            # ---------- severity assignment ----------
            is_s1 = (
                risk >= 1.3
                and valid_range
                and track.confidence >= S1_CONFIDENCE
            )
            # S1 fires on a single frame; S2 requires stability.
            is_s2 = risk >= S2_RISK_THRESHOLD and stability == 1.0

            if not (is_s1 or is_s2):
                continue

            if is_vehicle:
                candidates.append(
                    Hazard(
                        kind="vehicle",
                        level="urgent" if is_s1 else "caution",
                        severity="S1" if is_s1 else "S2",
                        bearing=bearing,
                        message_key="vehicle_nearby",
                        haptic="triple" if is_s1 else "none",
                        confidence=track.confidence,
                        range_band=band,
                        range_valid=valid_range,
                        track_id=track.track_id,
                    )
                )
            else:
                candidates.append(
                    Hazard(
                        kind="obstacle",
                        level="urgent" if is_s1 else "caution",
                        severity="S1" if is_s1 else "S2",
                        bearing=bearing,
                        message_key="person_ahead" if track.label == "person" else "obstacle_ahead",
                        haptic="triple" if is_s1 else "none",
                        confidence=track.confidence,
                        range_band=band,
                        range_valid=valid_range,
                        track_id=track.track_id,
                    )
                )

        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (item.severity == "S1", item.level == "urgent", item.confidence),
            reverse=True,
        )
        candidate = candidates[0]

        return candidate
