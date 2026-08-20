"""Operational TTS keys and the client awareness deny-list must stay aligned.

Hazard templates already have a dedicated parity suite. This file covers the operational
speech table (connection / camera / session) and the fail-closed prefix guard that both
phones apply before speaking server-composed look_summary text.
"""

import os
import re

from akshrava_backend.composer import look_summary, render
from akshrava_backend.domain import Hazard
from akshrava_backend.protocol import SUPPORTED_LANGUAGES

from test_speech_parity import kotlin_simple_templates, kotlin_source

PROTOCOL_CLIENT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../android/app/src/main/java/org/akshrava/app/ProtocolClient.kt",
    )
)
IOS_SESSION = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../ios/Akshrava/Akshrava/AssistSessionManager.swift",
    )
)

OPERATIONAL_KEYS = (
    "op_connected",
    "op_restored",
    "op_link_lost",
    "op_vision_unavailable",
    "op_model_unavailable",
    "op_cloud_fallback_unavailable",
    "op_server_shedding",
    "op_camera_dark",
    "op_camera_glare",
    "op_camera_blurry",
    "op_camera_failed",
    "op_camera_stalled",
    "op_analyze_failed",
    "op_access_revoked",
    "op_auth_failed",
    "op_session_taken_over",
    "op_look_unavailable",
    "op_starting",
    "op_starting_no_cpu_keepalive",
    "op_starting_no_screen_keepalive",
    "op_phone_tilted",
    "op_thermal_slow",
    "op_battery_low",
    "op_battery_critical",
    "op_power_keepalive_lost",
    "op_headset_disconnected",
    "op_env_dark",
    "op_env_bright",
)

FORBIDDEN_PREFIXES = ("saf", "clear", "cross", "navigat", "collis", "approach")
LANGUAGES = ("en", "hi", "ta", "kn", "ml", "te")


def _list_literal(source: str, name: str) -> list[str]:
    block = re.search(
        r"(?:val|let)\s+%s\s*=\s*(?:listOf\(([^)]*)\)|\[([^\]]*)\])" % re.escape(name),
        source,
    )
    assert block, "%s not found" % name
    body = block.group(1) or block.group(2)
    return re.findall(r'"([^"]+)"', body)


def test_every_operational_speech_key_exists_in_all_supported_languages():
    source = kotlin_source()
    for key in OPERATIONAL_KEYS:
        found = kotlin_simple_templates(source, key)
        for language in LANGUAGES:
            assert language in found, "%s missing %s" % (key, language)
            assert found[language].strip(), "%s/%s is blank" % (key, language)
    assert SUPPORTED_LANGUAGES == set(LANGUAGES)


def test_operational_speech_never_contains_a_forbidden_prefix():
    source = kotlin_source()
    for key in OPERATIONAL_KEYS:
        for language, text in kotlin_simple_templates(source, key).items():
            lower = text.lower()
            for prefix in FORBIDDEN_PREFIXES:
                assert prefix not in lower, "%s/%s contains %r: %s" % (key, language, prefix, text)


def test_android_and_ios_awareness_deny_lists_are_identical():
    with open(PROTOCOL_CLIENT, encoding="utf-8") as handle:
        android = handle.read()
    with open(IOS_SESSION, encoding="utf-8") as handle:
        ios = handle.read()
    android_list = _list_literal(android, "FORBIDDEN_AWARENESS_PREFIXES")
    ios_list = _list_literal(ios, "forbiddenAwarenessPrefixes")
    assert android_list == ios_list == list(FORBIDDEN_PREFIXES)


def test_deny_list_covers_the_required_prefixes():
    assert FORBIDDEN_PREFIXES == ("saf", "clear", "cross", "navigat", "collis", "approach")


def test_server_look_strings_are_speakable_under_the_client_deny_list():
    hazard = Hazard(
        kind="person",
        level="caution",
        bearing="ahead",
        message_key="person_ahead",
        haptic="single",
        confidence=0.9,
    )
    keys = ("obstacle_ahead", "person_ahead", "vehicle_nearby", "busy_road", "look_clear", "look_unavailable")
    for language in LANGUAGES:
        for key in keys:
            text = render(key, language)
            lower = text.lower()
            for prefix in FORBIDDEN_PREFIXES:
                assert prefix not in lower, "server %s/%s contains %r: %s" % (key, language, prefix, text)
        for checked in (True, False):
            summary = look_summary(hazard if checked else None, language, checked=checked)
            lower = summary.lower()
            for prefix in FORBIDDEN_PREFIXES:
                assert prefix not in lower, "look_summary %s checked=%s contains %r: %s" % (
                    language,
                    checked,
                    prefix,
                    summary,
                )
        empty = look_summary(None, language, checked=True)
        for prefix in FORBIDDEN_PREFIXES:
            assert prefix not in empty.lower(), "empty look %s contains %r: %s" % (language, prefix, empty)
