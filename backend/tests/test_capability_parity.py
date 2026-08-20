"""Cross-tier parity for the protocol capability tokens.

The capability strings are wire values that gate real behaviour: whether the phone may send the
full pose range, and whether it acknowledges results. They are declared independently in three
languages -- Python (``protocol.py``), Kotlin (``ProtocolClient.kt``) and Swift
(``Network/ProtocolClient.swift``) -- because no build step is shared across the three tiers.

A typo in any one copy does not fail a build anywhere. It fails *open*, silently and in the worst
direction: the client simply never sees the capability it was looking for, so it keeps the
conservative legacy behaviour forever. Nobody notices, because "the workaround stayed on" looks
exactly like "the workaround was still needed" -- which is precisely the trap the capability
negotiation was introduced to escape.

This lives in the backend suite deliberately: the backend CI job runs on every push, whereas the
Android and iOS jobs only run on pull requests.
"""

import os
import re

import pytest

from akshrava_backend.protocol import (
    POSE_CDEG_FULL_RANGE,
    RESULT_ACKNOWLEDGEMENT,
    SERVER_CAPABILITIES,
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

ANDROID_PROTOCOL_CLIENT = os.path.join(
    _REPO_ROOT, "android/app/src/main/java/org/akshrava/app/ProtocolClient.kt"
)
IOS_PROTOCOL_CLIENT = os.path.join(
    _REPO_ROOT, "ios/Akshrava/Akshrava/Network/ProtocolClient.swift"
)

# The server is the source of truth: it decides what a capability is called on the wire.
EXPECTED_CAPABILITIES = {
    "POSE_CDEG_FULL_RANGE": POSE_CDEG_FULL_RANGE,
    "RESULT_ACKNOWLEDGEMENT": RESULT_ACKNOWLEDGEMENT,
}


def _read(path: str) -> str:
    if not os.path.exists(path):
        pytest.skip("client source is not present in this checkout: %s" % path)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _kotlin_const(source: str, name: str) -> str | None:
    match = re.search(r'const\s+val\s+%s\s*=\s*"([^"]+)"' % re.escape(name), source)
    return match.group(1) if match else None


def _swift_static_let(source: str, name: str) -> str | None:
    match = re.search(
        r'static\s+let\s+%s\s*(?::\s*String\s*)?=\s*"([^"]+)"' % re.escape(name), source
    )
    return match.group(1) if match else None


def test_server_advertises_exactly_the_documented_capabilities():
    """A capability absent from SERVER_CAPABILITIES is never advertised, so clients never see it."""
    assert set(SERVER_CAPABILITIES) == set(EXPECTED_CAPABILITIES.values())


def test_android_capability_tokens_match_the_server():
    source = _read(ANDROID_PROTOCOL_CLIENT)
    assert _kotlin_const(source, "CAPABILITY_POSE_CDEG_FULL_RANGE") == POSE_CDEG_FULL_RANGE
    assert _kotlin_const(source, "CAPABILITY_RESULT_ACKNOWLEDGEMENT") == RESULT_ACKNOWLEDGEMENT


def test_ios_capability_tokens_match_the_server():
    source = _read(IOS_PROTOCOL_CLIENT)
    assert _swift_static_let(source, "capabilityPoseCdegFullRange") == POSE_CDEG_FULL_RANGE
    assert _swift_static_let(source, "capabilityResultAcknowledgement") == RESULT_ACKNOWLEDGEMENT


def test_clients_send_the_result_acknowledgement_header_field_the_server_parses():
    """The frame-header field name is a fourth independent copy of the same contract.

    The server reads `result_acknowledgement` off the frame header to decide whether a missing
    acknowledgement is meaningful. If a client spelled it differently the server would quietly
    treat that phone as a legacy client forever -- no error, just permanently absent telemetry.
    """
    for path in (ANDROID_PROTOCOL_CLIENT, IOS_PROTOCOL_CLIENT):
        assert '"result_acknowledgement"' in _read(path), (
            "client %s must send the result_acknowledgement header field" % path
        )


def test_result_ack_control_message_type_matches_across_clients():
    """`result_ack` is the message type the server dispatches on; a typo silently drops telemetry."""
    for path in (ANDROID_PROTOCOL_CLIENT, IOS_PROTOCOL_CLIENT):
        assert '"result_ack"' in _read(path), (
            "client %s must send the result_ack control message type" % path
        )
