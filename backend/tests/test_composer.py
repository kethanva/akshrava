from akshrava_backend.composer import hazard_payload, look_summary, render
from akshrava_backend.domain import Hazard


def test_look_summary_clear_and_hazard_never_claims_approach():
    # Checked empty view must not sound like "path is clear / safe".
    summary_clear = look_summary(None, "en")
    assert "alert" in summary_clear.lower()
    assert "cane" in summary_clear.lower() or "guide" in summary_clear.lower()
    assert "approach" not in summary_clear.lower()
    assert "safe" not in summary_clear.lower()
    # Ban the ambiguous word "clear" in user-facing look speech.
    assert "clear" not in summary_clear.lower()
    hazard = Hazard(
        kind="vehicle",
        level="caution",
        bearing="right",
        message_key="vehicle_nearby",
        haptic="none",
        confidence=0.9,
    )
    summary = look_summary(hazard, "en")
    assert "vehicle" in summary.lower()
    assert "approach" not in summary.lower()


def test_hindi_look_clear_language():
    text = look_summary(None, "hi")
    assert "अलर्ट" in text or "बेंत" in text or "गाइड" in text


def test_unchecked_look_never_claims_the_view_was_clear():
    # Regression test: hazard=None from a late-suppressed frame means "we never scored it", not
    # "we scored it and it was clear". Confidently reporting "no hazard" from unchecked evidence
    # is the same failure class the plan forbids for invented distance claims (§5.1).
    unchecked = look_summary(None, "en", checked=False)
    assert "clear" not in unchecked.lower()
    assert "no hazard" not in unchecked.lower()
    assert "alert" not in unchecked.lower() or "could not" in unchecked.lower()
    checked_empty = look_summary(None, "en", checked=True)
    assert "clear" not in checked_empty.lower()
    assert "cane" in checked_empty.lower() or "guide" in checked_empty.lower()


def test_hazard_payload_includes_spoken_preview():
    hazard = Hazard(
        kind="obstacle",
        level="caution",
        bearing="ahead",
        message_key="obstacle_ahead",
        haptic="none",
        confidence=0.8,
    )
    payload = hazard_payload(hazard, "en")
    assert payload["message_key"] == "obstacle_ahead"
    assert payload["motion_evidence"] == "insufficient"
    assert "Obstacle" in payload["spoken_preview"]
    assert render("vehicle_nearby", "en", "left") == "Vehicle nearby, left"
