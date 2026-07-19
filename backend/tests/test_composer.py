from akshrava_backend.composer import hazard_payload, look_summary, render
from akshrava_backend.domain import Hazard


def test_look_summary_clear_and_hazard_never_claims_approach():
    assert "clear" in look_summary(None, "en").lower()
    assert "approach" not in look_summary(None, "en").lower()
    assert "safe" not in look_summary(None, "en").lower()
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
    assert "खतरा" in look_summary(None, "hi") or "स्पष्ट" in look_summary(None, "hi")


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
