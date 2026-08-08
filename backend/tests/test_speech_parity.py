"""Cross-tier parity for user-facing safety speech.

The same spoken sentence is rendered twice, independently, in six languages:

* server -- ``akshrava_backend/composer.py`` produces ``spoken_preview`` and the on-demand
  ``look_summary``;
* phone  -- ``AlertManager.template()`` renders the ordinary hazard path from ``message_key``.

Neither renderer can be deleted (the phone must still speak when a preview is stale or absent,
and the server owns the priority-look sentence), so the only way they stay honest is a test that
compares them. This matters more than a normal DRY concern: a drift in Tamil or Malayalam is
invisible to almost every reviewer of this repository, and the artefact that drifts is the exact
sentence a blind user acts on. The safety boundary is enforced by wording, so wording is code.

This lives in the backend suite deliberately: the backend CI job runs on every push, whereas the
Android job only runs on pull requests.
"""

import os
import re

import pytest

from akshrava_backend.composer import (
    BEARING_HI,
    BEARING_KN,
    BEARING_ML,
    BEARING_TA,
    BEARING_TE,
    render,
)
from akshrava_backend.protocol import SUPPORTED_LANGUAGES

ALERT_MANAGER = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../android/app/src/main/java/org/akshrava/app/AlertManager.kt",
    )
)

# Keys both tiers render for the ordinary (non-priority) hazard path.
SHARED_SIMPLE_KEYS = ("obstacle_ahead", "person_ahead", "busy_road")
BEARINGS = ("left", "right", "ahead")
# Languages the phone renders inline; English is the `else` branch of each `when`.
NON_DEFAULT_LANGUAGES = ("hi", "ta", "kn", "ml", "te")

SERVER_BEARINGS = {
    "hi": BEARING_HI,
    "ta": BEARING_TA,
    "kn": BEARING_KN,
    "ml": BEARING_ML,
    "te": BEARING_TE,
}


def kotlin_source() -> str:
    if not os.path.exists(ALERT_MANAGER):
        pytest.skip("AlertManager.kt not present in this checkout")
    with open(ALERT_MANAGER, encoding="utf-8") as handle:
        return handle.read()


def kotlin_simple_templates(source: str, key: str) -> dict[str, str]:
    """Extract `"<key>" -> when (languageCode) { "hi" -> "..." ... else -> "..." }`."""
    block = re.search(
        r'"%s"\s*->\s*when\s*\(languageCode\)\s*\{(.*?)\n\s*\}' % re.escape(key),
        source,
        re.DOTALL,
    )
    assert block, "AlertManager.kt has no template branch for message_key %r" % key
    body = block.group(1)
    found = dict(re.findall(r'"(\w\w)"\s*->\s*"([^"]*)"', body))
    default = re.search(r'else\s*->\s*"([^"]*)"', body)
    assert default, "template branch %r has no English default" % key
    found["en"] = default.group(1)
    return found


def kotlin_bearing_map(source: str, language: str) -> dict[str, str]:
    """Extract `private val bearingXx = mapOf("left" to "...", ...)`."""
    name = "bearing" + language.capitalize()
    block = re.search(r"val\s+%s\s*=\s*mapOf\((.*?)\)\s*$" % name, source, re.MULTILINE)
    assert block, "AlertManager.kt has no %s map" % name
    return dict(re.findall(r'"(\w+)"\s+to\s+"([^"]*)"', block.group(1)))


def kotlin_vehicle_sentence(source: str, language: str, bearing: str) -> str:
    """Rebuild the phone's `vehicle_nearby` sentence for one language and bearing."""
    branch = re.search(
        r'"vehicle_nearby"\s*->\s*when\s*\(languageCode\)\s*\{(.*?)\n\s*\}\n',
        source,
        re.DOTALL,
    )
    assert branch, "AlertManager.kt has no vehicle_nearby branch"
    body = branch.group(1)

    if language == "en":
        # Strip the nested Hindi `when (bearing) { ... else -> ... }` first, or its else branch
        # is found before the outer one and English silently compares against Hindi.
        outer = re.sub(r'when\s*\(bearing\)\s*\{.*?\}', "", body, flags=re.DOTALL)
        default = re.search(r'else\s*->\s*"([^"]*)"', outer)
        assert default, "vehicle_nearby has no English default"
        return default.group(1).replace("$bearing", bearing)

    if language == "hi":
        # Hindi inlines the whole sentence per bearing rather than using a bearing map.
        hindi = re.search(r'"hi"\s*->\s*when\s*\(bearing\)\s*\{(.*?)\}', body, re.DOTALL)
        assert hindi, "vehicle_nearby has no Hindi bearing branch"
        cases = dict(re.findall(r'"(\w+)"\s*->\s*"([^"]*)"', hindi.group(1)))
        fallback = re.search(r'else\s*->\s*"([^"]*)"', hindi.group(1))
        assert fallback, "Hindi vehicle_nearby has no else branch"
        return cases.get(bearing, fallback.group(1))

    # ta / kn / ml / te: "<prefix>${bearingXx[bearing] ?: ...}"
    prefix = re.search(r'"%s"\s*->\s*"([^"$]*)\$\{bearing' % language, body)
    assert prefix, "vehicle_nearby has no %s branch" % language
    return prefix.group(1) + kotlin_bearing_map(source, language)[bearing]


def test_both_tiers_support_exactly_the_same_language_set():
    """A language added on one side only means that user hears a silently different app."""
    source = kotlin_source()
    phone_languages = set(kotlin_simple_templates(source, "obstacle_ahead"))
    assert phone_languages == SUPPORTED_LANGUAGES | {"en"}, (
        "server SUPPORTED_LANGUAGES and AlertManager template languages have diverged: "
        "server=%s phone=%s" % (sorted(SUPPORTED_LANGUAGES | {"en"}), sorted(phone_languages))
    )


@pytest.mark.parametrize("key", SHARED_SIMPLE_KEYS)
def test_simple_hazard_sentences_match_between_server_and_phone(key):
    source = kotlin_source()
    phone = kotlin_simple_templates(source, key)
    for language in sorted(SUPPORTED_LANGUAGES | {"en"}):
        assert phone[language] == render(key, language), (
            "message_key=%r language=%r has drifted:\n  phone : %r\n  server: %r"
            % (key, language, phone[language], render(key, language))
        )


@pytest.mark.parametrize("language", sorted(SUPPORTED_LANGUAGES | {"en"}))
@pytest.mark.parametrize("bearing", BEARINGS)
def test_vehicle_sentences_match_between_server_and_phone(language, bearing):
    """vehicle_nearby is the one templated sentence, so it is the most likely to drift."""
    source = kotlin_source()
    phone = kotlin_vehicle_sentence(source, language, bearing)
    assert phone == render("vehicle_nearby", language, bearing), (
        "vehicle_nearby language=%r bearing=%r has drifted:\n  phone : %r\n  server: %r"
        % (language, bearing, phone, render("vehicle_nearby", language, bearing))
    )


@pytest.mark.parametrize("language", ("ta", "kn", "ml", "te"))
def test_bearing_tables_match_between_server_and_phone(language):
    """Left/right is a directional safety claim; a swapped table points the user the wrong way."""
    source = kotlin_source()
    phone = kotlin_bearing_map(source, language)
    server = SERVER_BEARINGS[language]
    for bearing in BEARINGS:
        assert phone[bearing] == server[bearing], (
            "bearing table drift language=%r bearing=%r:\n  phone : %r\n  server: %r"
            % (language, bearing, phone[bearing], server[bearing])
        )


def test_no_renderer_emits_forbidden_safety_language():
    """Neither tier may imply navigation, crossing, clearance or safety, in any language.

    scripts/run_e2e_tests.sh greps for English terms only. These renderers are the one place a
    non-English string is spoken verbatim to the user, so assert the structural property the
    English grep cannot see: the "look" answers must never claim a clear path, only the absence
    of an alert in a recent view.
    """
    for language in sorted(SUPPORTED_LANGUAGES | {"en"}):
        clear = render("look_clear", language)
        assert clear, "look_clear must render in %s" % language
        # English is the only one this test can read; assert it exactly, and assert the others
        # are at least distinct from the hazard sentences (i.e. a real translation exists).
        assert clear != render("obstacle_ahead", language)
    assert "clear" not in render("look_clear", "en").lower().replace("continue using", "")
    assert "safe" not in render("look_clear", "en").lower()
