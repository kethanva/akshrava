from typing import Iterable, List

from .domain import Detection, Track


def _iou(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


class SimpleTracker:
    """Low-rate tracker helper for persistence only; never infers approach speed.

    VisionService keeps one SimpleTracker per WebSocket session (keyed by device_id)
    so track ID counters do not collide across concurrent phones. Association state
    lives on SessionState.tracks — reconnecting opens a fresh session tracker.
    Implements ByteTrack-style two-stage association without a Kalman filter.
    """

    def __init__(self):
        self._next_id = 1

    def update(
        self,
        tracks: List[Track],
        detections: Iterable[Detection],
        *,
        discard_missed: bool = False,
    ) -> List[Track]:
        unmatched_dets = list(detections)
        unmatched_tracks = list(tracks)
        next_tracks = []
        
        # Split detections by confidence
        high_dets = [d for d in unmatched_dets if d.confidence >= 0.5]
        low_dets = [d for d in unmatched_dets if d.confidence < 0.5]
        
        # Stage 1: Match high-confidence detections
        for track in list(unmatched_tracks):
            candidates = [d for d in high_dets if d.label == track.label]
            best = max(candidates, key=lambda d: _iou(track.box, d.box), default=None)
            if best is not None and _iou(track.box, best.box) >= 0.3:
                high_dets.remove(best)
                unmatched_dets.remove(best)
                unmatched_tracks.remove(track)
                track.box = best.box
                track.confidence = best.confidence
                track.hits += 1
                track.missed = 0
                next_tracks.append(track)
                
        # Stage 2: Match remaining tracks with low-confidence detections
        for track in list(unmatched_tracks):
            candidates = [d for d in low_dets if d.label == track.label]
            best = max(candidates, key=lambda d: _iou(track.box, d.box), default=None)
            if best is not None and _iou(track.box, best.box) >= 0.2:
                low_dets.remove(best)
                unmatched_dets.remove(best)
                unmatched_tracks.remove(track)
                track.box = best.box
                track.confidence = best.confidence
                track.hits += 1
                track.missed = 0
                next_tracks.append(track)
                
        # A large fresh IMU pose jump makes a prior image-space box unreliable. Do not invent a
        # pixel translation from uncalibrated IMU values; discard only unmatched stale tracks and
        # let the current image establish a new one. Normal small movement retains persistence.
        if not discard_missed:
            for track in unmatched_tracks:
                track.missed += 1
                if track.missed < 5:
                    next_tracks.append(track)
                
        # Create new tracks from remaining unmatched high-confidence detections
        for detection in unmatched_dets:
            if detection.confidence >= 0.5:
                next_tracks.append(
                    Track(
                        track_id=self._next_id,
                        label=detection.label,
                        confidence=detection.confidence,
                        box=detection.box,
                    )
                )
                self._next_id += 1
                
        return next_tracks
