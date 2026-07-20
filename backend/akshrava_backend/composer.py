"""Alert template table. Emits message_key + slots; phone TTS / clips render speech."""

from typing import Dict, Optional

from .domain import Hazard


TEMPLATES_EN = {
    "obstacle_ahead": "Obstacle ahead",
    "vehicle_nearby": "Vehicle nearby, {bearing}",
    "busy_road": "Busy road, careful",
    # Never imply the path is safe/clear — silence never means safety (architecture §1).
    "look_clear": "No alert in this recent view. Continue using cane or guide",
    "look_unavailable": "Could not check just now, try again",
}

TEMPLATES_HI = {
    "obstacle_ahead": "आगे रुकावट",
    "vehicle_nearby": "वाहन {bearing_hi}",
    "busy_road": "व्यस्त सड़क, सावधान",
    "look_clear": "इस हाल के दृश्य में कोई अलर्ट नहीं. बेंत या गाइड का उपयोग जारी रखें",
    "look_unavailable": "अभी जांच नहीं हो सकी, फिर कोशिश करें",
}

BEARING_HI = {"left": "बाईं ओर है", "right": "दाईं ओर है", "ahead": "आगे है"}


def render(message_key: str, language: str = "en", bearing: str = "ahead") -> str:
    table = TEMPLATES_HI if language.startswith("hi") else TEMPLATES_EN
    template = table.get(message_key, table.get("obstacle_ahead", "Assistance limited"))
    return template.format(
        bearing=bearing,
        bearing_hi=BEARING_HI.get(bearing, BEARING_HI["ahead"]),
    )


def hazard_payload(hazard: Hazard, language: str = "en") -> Dict:
    """Wire payload: template ID + slots. Never includes approach/cross advice."""
    return {
        "kind": hazard.kind,
        "level": hazard.level,
        "severity": hazard.severity,
        "bearing": hazard.bearing,
        "message_key": hazard.message_key,
        "haptic": hazard.haptic,
        "confidence": round(hazard.confidence, 3),
        "range_band": hazard.range_band if hazard.range_valid else "unknown",
        "range_valid": hazard.range_valid,
        "motion_evidence": "insufficient",
        "spoken_preview": render(hazard.message_key, language, hazard.bearing),
    }


def look_summary(hazard: Optional[Hazard], language: str = "en", checked: bool = True) -> str:
    """On-demand look: one composed sentence for this moment.

    `checked=False` means the frame was never scored (e.g. late-suppressed past the freshness
    budget) -- hazard=None in that case does not mean "we looked and it was clear", it means
    "we didn't look". Confidently reporting "no hazard" from unchecked evidence is exactly the
    failure mode the plan forbids for distance claims (§5.1); the same principle applies here.
    """
    if not checked:
        return render("look_unavailable", language)
    if hazard is None:
        return render("look_clear", language)
    return render(hazard.message_key, language, hazard.bearing)
