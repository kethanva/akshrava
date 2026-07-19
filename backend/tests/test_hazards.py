from akshrava_backend.domain import Detection, GeometryProfile, SessionState
from akshrava_backend.hazards import HazardScorer, _ground_plane_distance
from akshrava_backend.tracker import SimpleTracker


CENTRAL_BOX = (220, 100, 430, 460)


def _single_frame_track(label="person", confidence=0.9):
    return SimpleTracker().update([], [Detection(label=label, confidence=confidence, box=CENTRAL_BOX)])


def _stable_track(label="person", confidence=0.9):
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label=label, confidence=confidence, box=CENTRAL_BOX)])
    return tracker.update(tracks, [Detection(label=label, confidence=confidence, box=CENTRAL_BOX)])


def test_stable_large_central_person_gets_obstacle_alert():
    state = SessionState(device_id="test", tracks=_stable_track())
    hazard = HazardScorer().score(state, 640, 480, 20, -1200, 0)
    assert hazard is not None
    assert hazard.message_key == "obstacle_ahead"
    assert not hazard.range_valid
    assert hazard.level == "caution"


def test_invalid_pose_downgrades_range_and_urgency():
    state = SessionState(device_id="test", tracks=_stable_track())
    hazard = HazardScorer().score(state, 640, 480, 1000, -1200, 0)
    assert hazard is not None
    assert not hazard.range_valid
    assert hazard.level == "caution"


def test_vehicle_never_claims_approach():
    state = SessionState(device_id="test", tracks=_stable_track("car"))
    hazard = HazardScorer().score(state, 640, 480, 20, -1200, 0)
    assert hazard is not None
    assert hazard.message_key == "vehicle_nearby"
    assert "approach" not in hazard.message_key


def test_unimplemented_calibration_never_enables_single_frame_s1():
    state = SessionState(device_id="test", tracks=_single_frame_track(confidence=0.9))
    hazard = HazardScorer().score(state, 640, 480, 20, -1200, 0)
    assert hazard is None


def test_below_s1_confidence_still_needs_two_frames():
    # 0.5 clears the alert floor but is under the S1 threshold, so one frame stays silent.
    one_frame = SessionState(device_id="test", tracks=_single_frame_track(confidence=0.5))
    assert HazardScorer().score(one_frame, 640, 480, 20, -1200, 0) is None
    two_frames = SessionState(device_id="test", tracks=_stable_track(confidence=0.5))
    hazard = HazardScorer().score(two_frames, 640, 480, 20, -1200, 0)
    assert hazard is not None
    assert hazard.severity == "S2"


def test_invalid_range_never_produces_single_frame_s1():
    # A high-confidence detection with an untrusted pose must not become a single-frame S1.
    state = SessionState(device_id="test", tracks=_single_frame_track(confidence=0.9))
    assert HazardScorer().score(state, 640, 480, 1000, -1200, 0) is None


def test_one_device_cannot_exhaust_another_devices_alert_budget():
    scorer = HazardScorer()
    first = SessionState(device_id="first", tracks=_stable_track())
    second = SessionState(device_id="second", tracks=_stable_track())
    for index in range(6):
        first.last_alert_at_ms.clear()
        assert scorer.score(first, 640, 480, 20, -1200, 0) is not None, index
    assert scorer.score(second, 640, 480, 20, -1200, 0) is not None

def test_global_rate_limit():
    scorer = HazardScorer()
    state = SessionState(device_id="test", tracks=_stable_track())
    for _ in range(6):
        state.last_alert_at_ms.clear() # clear per-key
        assert scorer.score(state, 640, 480, 20, -1200, 0) is not None
    state.last_alert_at_ms.clear()
    assert scorer.score(state, 640, 480, 20, -1200, 0) is None


def test_upward_pitch_never_becomes_a_downward_ground_plane_estimate():
    # A box at the optical centre has no pixel-angle contribution. With the camera tilted up,
    # its ray cannot intersect the ground in front of it. Treat that as invalid, not as a near
    # result produced by abs(pitch).
    profile = GeometryProfile("verified-r0", 500.0, 1.35)
    assert _ground_plane_distance((100, 100, 200, 240), 480, 1_000, profile) is None


def test_only_a_verified_geometry_profile_can_enable_range_validation():
    profile = GeometryProfile("verified-r0", 500.0, 1.35)
    state = SessionState(device_id="test", tracks=_single_frame_track(confidence=0.9), geometry_profile=profile)
    hazard = HazardScorer().score(state, 640, 480, 20, -1_200, 0, profile)
    # Even a verified profile must satisfy every pose/range gate; this fixture's one-frame S1
    # is allowed only when those values agree, never simply because an ID was supplied.
    assert hazard is None or hazard.range_valid
    
