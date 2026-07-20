from akshrava_backend.domain import Detection, GeometryProfile, SessionState
from akshrava_backend.hazards import (
    HazardScorer,
    _ground_plane_distance,
    _pinhole_distance,
)
from akshrava_backend.alert_policy import ALERT_DEBOUNCE_MS, AlertPolicy
from akshrava_backend.tracker import SimpleTracker


CENTRAL_BOX = (220, 100, 430, 460)
HALF_CENTRAL_BOX = tuple(v / 2.0 for v in CENTRAL_BOX)


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
    assert hazard.message_key == "person_ahead"
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
    policy = AlertPolicy()
    first = SessionState(device_id="first", tracks=_stable_track())
    second = SessionState(device_id="second", tracks=_stable_track())
    for index in range(6):
        first.last_alert_at_ms.clear()
        assert policy.admit(first, scorer.score(first, 640, 480, 20, -1200, 0), priority=False) is not None, index
    assert policy.admit(second, scorer.score(second, 640, 480, 20, -1200, 0), priority=False) is not None

def test_global_rate_limit():
    scorer = HazardScorer()
    policy = AlertPolicy()
    state = SessionState(device_id="test", tracks=_stable_track())
    for _ in range(6):
        state.last_alert_at_ms.clear() # clear per-key
        assert policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False) is not None
    state.last_alert_at_ms.clear()
    assert policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False) is None


def test_upward_pitch_never_becomes_a_downward_ground_plane_estimate():
    # A box at the optical centre has no pixel-angle contribution. With the camera tilted up,
    # its ray cannot intersect the ground in front of it. Treat that as invalid, not as a near
    # result produced by abs(pitch).
    profile = GeometryProfile("verified-r0", 500.0, 1.35)
    assert _ground_plane_distance((100, 100, 200, 240), 480, 1_000, profile) is None


def test_focal_scales_with_frame_height_so_quality_downscale_preserves_range():
    # Without scaling, half-resolution boxes with a static focal inflate distance ~2x and can
    # push a near hazard out of the near band while still passing the agreement ratio.
    profile = GeometryProfile("verified-r0", 500.0, 1.35, reference_height_px=480)
    full = _pinhole_distance("person", CENTRAL_BOX, 480, profile)
    half = _pinhole_distance("person", HALF_CENTRAL_BOX, 240, profile)
    assert full is not None and half is not None
    assert abs(full - half) < 0.05

    ground_full = _ground_plane_distance(CENTRAL_BOX, 480, -1_200, profile)
    ground_half = _ground_plane_distance(HALF_CENTRAL_BOX, 240, -1_200, profile)
    assert ground_full is not None and ground_half is not None
    assert abs(ground_full - ground_half) < 0.05


def test_unscaled_focal_at_half_resolution_inflates_pinhole_distance():
    # Control: treating focal as resolution-invariant doubles distance at half height.
    profile = GeometryProfile("verified-r0", 500.0, 1.35, reference_height_px=480)
    full = _pinhole_distance("person", CENTRAL_BOX, 480, profile)
    naive_half = 500.0 * 1.65 / max(0.0, HALF_CENTRAL_BOX[3] - HALF_CENTRAL_BOX[1])
    assert full is not None
    assert naive_half > full * 1.8


def test_server_same_key_debounce_is_short_phone_owns_speech_cooldown():
    # Phone AlertManager owns the 5s object cooldown; server only debounces ~800ms.
    assert ALERT_DEBOUNCE_MS == 800
    assert ALERT_DEBOUNCE_MS < 5_000
    scorer = HazardScorer()
    policy = AlertPolicy()
    state = SessionState(device_id="test", tracks=_stable_track())
    first = policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False)
    assert first is not None
    blocked = policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False)
    assert blocked is None
    # Simulate debounce expiry without waiting wall clock.
    key = "%s:%s" % (first.kind, first.bearing)
    state.last_alert_at_ms[key] = state.last_alert_at_ms[key] - ALERT_DEBOUNCE_MS - 1
    again = policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False)
    assert again is not None


def test_only_a_verified_geometry_profile_can_enable_range_validation():
    profile = GeometryProfile("verified-r0", 500.0, 1.35)
    state = SessionState(device_id="test", tracks=_single_frame_track(confidence=0.9), geometry_profile=profile)
    hazard = HazardScorer().score(state, 640, 480, 20, -1_200, 0, profile)
    # Even a verified profile must satisfy every pose/range gate; this fixture's one-frame S1
    # is allowed only when those values agree, never simply because an ID was supplied.
    assert hazard is None or hazard.range_valid


def test_priority_skip_cooldowns_returns_hazard():
    state = SessionState(device_id="test", tracks=_stable_track())
    scorer = HazardScorer()
    policy = AlertPolicy()
    first = policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False)
    assert first is not None
    blocked = policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False)
    assert blocked is None
    looked = policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0, skip_cooldowns=True), priority=True)
    assert looked is not None
    assert looked.message_key == first.message_key
    assert "approach" not in looked.message_key


def test_priority_skip_cooldowns_bypasses_device_rate_limit():
    scorer = HazardScorer()
    policy = AlertPolicy()
    state = SessionState(device_id="test", tracks=_stable_track())
    for _ in range(6):
        state.last_alert_at_ms.clear()
        assert policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False) is not None
    state.last_alert_at_ms.clear()
    assert policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0), priority=False) is None
    assert policy.admit(state, scorer.score(state, 640, 480, 20, -1200, 0, skip_cooldowns=True), priority=True) is not None


def test_scorer_is_pure_and_never_mutates_alert_delivery_state():
    state = SessionState(device_id="test", tracks=_stable_track())
    before = (dict(state.last_alert_at_ms), list(state.alert_timestamps_ms))
    assert HazardScorer().score(state, 640, 480, 20, -1200, 0) is not None
    assert (state.last_alert_at_ms, state.alert_timestamps_ms) == before


def test_on_demand_look_answers_a_never_before_seen_object_on_its_first_frame():
    # Regression test: an on-demand look is, by construction, exactly one pulled frame -- there
    # is no second observation coming. The scorer used to require two-frame persistence
    # (track.hits >= 2) unconditionally, so a look query could never report a hazard for
    # anything the tracker hadn't already seen twice -- defeating the feature's entire purpose,
    # which is precisely the standing-still blind spot where ambient capture hasn't looked twice.
    state = SessionState(device_id="test", tracks=_single_frame_track(confidence=0.9))
    assert HazardScorer().score(state, 640, 480, 20, -1200, 0) is None  # ambient: still silent
    looked = HazardScorer().score(state, 640, 480, 20, -1200, 0, skip_cooldowns=True)
    assert looked is not None
    assert looked.message_key == "person_ahead"
