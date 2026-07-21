"""Alert template table. Emits message_key + slots; phone TTS / clips render speech."""

from typing import Dict, Optional

from .domain import Hazard


TEMPLATES_EN = {
    "obstacle_ahead": "Obstacle ahead",
    "person_ahead": "Person ahead",
    "vehicle_nearby": "Vehicle nearby, {bearing}",
    "busy_road": "Busy road, careful",
    # Never imply the path is safe/clear — silence never means safety (architecture §1).
    "look_clear": "No alert in this recent view. Continue using cane or guide",
    "look_unavailable": "Could not check just now, try again",
}

TEMPLATES_HI = {
    "obstacle_ahead": "आगे रुकावट",
    "person_ahead": "आगे व्यक्ति",
    "vehicle_nearby": "वाहन {bearing_hi}",
    "busy_road": "व्यस्त सड़क, सावधान",
    "look_clear": "इस हाल के दृश्य में कोई अलर्ट नहीं. बेंत या गाइड का उपयोग जारी रखें",
    "look_unavailable": "अभी जांच नहीं हो सकी, फिर कोशिश करें",
}

TEMPLATES_TA = {
    "obstacle_ahead": "முன்னே தடையுள்ளது", "person_ahead": "முன்னே நபர் உள்ளார்",
    "vehicle_nearby": "அருகில் வாகனம் {bearing_ta}", "busy_road": "பரபரப்பான சாலை, கவனம்",
    "look_clear": "சமீபத்திய பார்வையில் எச்சரிக்கை எதுவுமில்லை", "look_unavailable": "தற்போது சரிபார்க்க முடியவில்லை, மீண்டும் முயற்சிக்கவும்",
}
TEMPLATES_KN = {
    "obstacle_ahead": "ಮುಂದೆ ಅಡಚಣೆ ಇದೆ", "person_ahead": "ಮುಂದೆ ವ್ಯಕ್ತಿ ಇದ್ದಾರೆ",
    "vehicle_nearby": "ಹತ್ತಿರ ವಾಹನ {bearing_kn}", "busy_road": "ಗಿಜಿಗುಡಿದ ರಸ್ತೆ, ಎಚ್ಚರಿಕೆ",
    "look_clear": "ಇತ್ತೀಚಿನ ದೃಶ್ಯದಲ್ಲಿ ಎಚ್ಚರಿಕೆ ಇಲ್ಲ", "look_unavailable": "ಈಗ ಪರಿಶೀಲಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ, ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ",
}
TEMPLATES_ML = {
    "obstacle_ahead": "മുന്നിൽ തടസ്സമുണ്ട്", "person_ahead": "മുന്നിൽ വ്യക്തിയുണ്ട്",
    "vehicle_nearby": "അടുത്ത് വാഹനം {bearing_ml}", "busy_road": "തിരക്കേറിയ റോഡ്, ശ്രദ്ധിക്കുക",
    "look_clear": "സമീപകാല കാഴ്ചയിൽ മുന്നറിയിപ്പില്ല", "look_unavailable": "ഇപ്പോൾ പരിശോധിക്കാൻ കഴിഞ്ഞില്ല, വീണ്ടും ശ്രമിക്കുക",
}
TEMPLATES_TE = {
    "obstacle_ahead": "ముందు అడ్డంకి ఉంది", "person_ahead": "ముందు వ్యక్తి ఉన్నారు",
    "vehicle_nearby": "సమీపంలో వాహనం {bearing_te}", "busy_road": "రద్దీగా ఉన్న రహదారి, జాగ్రత్త",
    "look_clear": "ఇటీవలి దృశ్యంలో హెచ్చరిక లేదు", "look_unavailable": "ఇప్పుడు తనిఖీ చేయలేకపోయాం, మళ్లీ ప్రయత్నించండి",
}

BEARING_HI = {"left": "बाईं ओर है", "right": "दाईं ओर है", "ahead": "आगे है"}
BEARING_TA = {"left": "இடப்புறம் உள்ளது", "right": "வலப்புறம் உள்ளது", "ahead": "முன்னே உள்ளது"}
BEARING_KN = {"left": "ಎಡಭಾಗದಲ್ಲಿದೆ", "right": "ಬಲಭಾಗದಲ್ಲಿದೆ", "ahead": "ಮುಂದೆ ಇದೆ"}
BEARING_ML = {"left": "ഇടതുവശത്തുണ്ട്", "right": "വലതുവശത്തുണ്ട്", "ahead": "മുന്നിലുണ്ട്"}
BEARING_TE = {"left": "ఎడమ వైపున ఉంది", "right": "కుడి వైపున ఉంది", "ahead": "ముందు ఉంది"}


def render(message_key: str, language: str = "en", bearing: str = "ahead") -> str:
    language = language.lower()
    table = {
        "hi": TEMPLATES_HI, "ta": TEMPLATES_TA, "kn": TEMPLATES_KN,
        "ml": TEMPLATES_ML, "te": TEMPLATES_TE,
    }.get(language[:2], TEMPLATES_EN)
    template = table.get(message_key, table.get("obstacle_ahead", "Assistance limited"))
    return template.format(
        bearing=bearing,
        bearing_hi=BEARING_HI.get(bearing, BEARING_HI["ahead"]),
        bearing_ta=BEARING_TA.get(bearing, BEARING_TA["ahead"]),
        bearing_kn=BEARING_KN.get(bearing, BEARING_KN["ahead"]),
        bearing_ml=BEARING_ML.get(bearing, BEARING_ML["ahead"]),
        bearing_te=BEARING_TE.get(bearing, BEARING_TE["ahead"]),
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
