from akshrava_backend.domain import Detection
from akshrava_backend.tracker import SimpleTracker


BOX = (100.0, 100.0, 200.0, 200.0)


def test_low_confidence_detection_never_births_a_new_track():
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label="person", confidence=0.3, box=BOX)])
    assert tracks == []


def test_high_confidence_detection_births_a_track_with_one_hit():
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label="person", confidence=0.9, box=BOX)])
    assert len(tracks) == 1
    assert tracks[0].hits == 1
    assert tracks[0].missed == 0


def test_stage_two_rescues_a_track_with_only_a_low_confidence_match():
    # An object's detection confidence can dip between frames without the object having moved
    # or vanished. ByteTrack-style two-stage association exists precisely so a track survives
    # a low-confidence frame instead of being dropped and re-birthed with a fresh id.
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label="person", confidence=0.9, box=BOX)])
    track_id = tracks[0].track_id
    tracks = tracker.update(tracks, [Detection(label="person", confidence=0.3, box=BOX)])
    assert len(tracks) == 1
    assert tracks[0].track_id == track_id
    assert tracks[0].hits == 2
    assert tracks[0].missed == 0
    assert tracks[0].confidence == 0.3


def test_mismatched_label_never_matches_regardless_of_box_overlap():
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label="person", confidence=0.9, box=BOX)])
    person_id = tracks[0].track_id
    # Exact same box, different label: must not be treated as the same object.
    tracks = tracker.update(tracks, [Detection(label="car", confidence=0.9, box=BOX)])
    labels_by_id = {track.track_id: track.label for track in tracks}
    assert labels_by_id[person_id] == "person"
    assert any(track.label == "car" and track.track_id != person_id for track in tracks)


def test_greedy_match_prefers_higher_iou_candidate():
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label="person", confidence=0.9, box=BOX)])
    track_id = tracks[0].track_id
    close_match = Detection(label="person", confidence=0.9, box=(100.0, 100.0, 200.0, 200.0))
    far_match = Detection(label="person", confidence=0.9, box=(140.0, 140.0, 260.0, 260.0))
    tracks = tracker.update(tracks, [far_match, close_match])
    matched = next(track for track in tracks if track.track_id == track_id)
    assert matched.box == close_match.box
    # The unclaimed high-confidence detection births a second, independent track.
    assert len(tracks) == 2


def test_unmatched_track_is_dropped_exactly_on_the_fifth_consecutive_miss():
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label="person", confidence=0.9, box=BOX)])
    for expected_missed in range(1, 5):
        tracks = tracker.update(tracks, [])
        assert len(tracks) == 1
        assert tracks[0].missed == expected_missed
    tracks = tracker.update(tracks, [])
    assert tracks == []


def test_hits_increments_monotonically_across_repeated_matches():
    tracker = SimpleTracker()
    tracks = tracker.update([], [Detection(label="person", confidence=0.9, box=BOX)])
    for expected_hits in range(2, 5):
        tracks = tracker.update(tracks, [Detection(label="person", confidence=0.9, box=BOX)])
        assert tracks[0].hits == expected_hits
