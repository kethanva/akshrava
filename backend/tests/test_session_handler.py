"""Framing and admission tests for FrameStreamHandler.

Safety boundary: these cover transport framing and admission only — object/vehicle awareness
plumbing, never navigation, crossing, collision, approach-speed, or clear-path behaviour.
"""

import dataclasses
import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from akshrava_backend.config import Settings
from akshrava_backend.domain import FrameHeader, SessionState
from akshrava_backend.metrics import Metrics
from akshrava_backend.protocol import ProtocolError
from akshrava_backend.rate_limit import FrameRateLimiter
from akshrava_backend.session_handler import MAX_CONTROL_MESSAGE_BYTES, FrameStreamHandler

DEVICE_ID = "test-device-123"


@pytest.fixture
def handler_settings(monkeypatch):
    # No REDIS_URL, so the in-memory limiter path is the default under test. The test that needs
    # the Redis path sets redis_url explicitly via dataclasses.replace.
    monkeypatch.delenv("REDIS_URL", raising=False)
    return dataclasses.replace(
        Settings.from_env(),
        max_image_bytes=200_000,
        max_frame_side=1280,
        min_frame_interval_ms=50,
    )


@pytest.fixture
def store():
    mock = MagicMock()
    mock.is_device_revoked = AsyncMock(return_value=False)
    return mock


@pytest.fixture
def make_handler(handler_settings, store):
    def _make(settings=None, rate_allowed=True, limiter_error=None):
        device_rate_limiter = MagicMock()
        if limiter_error is not None:
            device_rate_limiter.allow = AsyncMock(side_effect=limiter_error)
        else:
            device_rate_limiter.allow = AsyncMock(return_value=rate_allowed)
        return FrameStreamHandler(
            device_id=DEVICE_ID,
            state=SessionState(device_id=DEVICE_ID),
            settings=settings or handler_settings,
            store=store,
            device_rate_limiter=device_rate_limiter,
            metrics=Metrics(),
            # Generous local buckets so each assertion's outcome comes from its own subject
            # (interval, monotonicity, revocation) rather than incidental token-bucket drain.
            local_limiter=FrameRateLimiter(1000.0, 1000.0),
            priority_local_limiter=FrameRateLimiter(1000.0, 1000.0),
            normal_rate=1.2,
            normal_burst=2.0,
            priority_rate=3.0,
            priority_burst=3.0,
        )

    return _make


@pytest.fixture
def handler(make_handler):
    return make_handler()


def frame_payload(**overrides):
    """A wire-format frame header. Note the short keys: `id`, `w`, `h` — not `frame_id`/`width`."""
    payload = {
        "type": "frame",
        "id": 1,
        "capture_mono_ms": 1_000,
        "jpeg_bytes": 1234,
        "w": 640,
        "h": 480,
    }
    payload.update(overrides)
    return json.dumps(payload)


def header(**overrides):
    fields = {
        "frame_id": 1,
        "capture_mono_ms": 1_000,
        "capture_epoch_ms": None,
        "width": 640,
        "height": 480,
        "jpeg_bytes": 1234,
        "calibration_id": "",
        "pitch_cdeg": None,
        "roll_cdeg": None,
        "pose_age_ms": None,
        "mode": "normal",
    }
    fields.update(overrides)
    return FrameHeader(**fields)


def jpeg_of(width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="blue").save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Control messages
# ---------------------------------------------------------------------------


async def test_ping_status_and_look_are_acknowledged(handler):
    assert await handler.handle_text_frame(json.dumps({"type": "ping"})) == {"type": "pong"}
    assert await handler.handle_text_frame(json.dumps({"type": "status"})) == {"type": "status_ack"}
    assert await handler.handle_text_frame(json.dumps({"type": "look"})) == {"type": "look_ack"}


async def test_unknown_message_type_is_reported_not_fatal(handler):
    assert await handler.handle_text_frame(json.dumps({"type": "nope"})) == {
        "type": "error",
        "code": "unknown_message",
    }


async def test_oversized_control_message_is_a_protocol_error(handler):
    oversized = json.dumps({"type": "ping", "pad": "x" * MAX_CONTROL_MESSAGE_BYTES})
    with pytest.raises(ProtocolError):
        await handler.handle_text_frame(oversized)


async def test_non_object_control_message_is_a_protocol_error(handler):
    with pytest.raises(ProtocolError):
        await handler.handle_text_frame(json.dumps(["not", "an", "object"]))


# ---------------------------------------------------------------------------
# Header admission
# ---------------------------------------------------------------------------


async def test_valid_header_is_held_pending_its_binary(handler):
    assert await handler.handle_text_frame(frame_payload()) is None
    assert handler.pending_header is not None
    assert handler.pending_header.width == 640


async def test_revoked_device_closes_the_socket(make_handler, store):
    store.is_device_revoked = AsyncMock(return_value=True)
    handler = make_handler()
    assert await handler.handle_text_frame(frame_payload()) == {"_action": "close", "code": 4403}


async def test_header_before_prior_binary_is_a_protocol_violation(handler):
    await handler.handle_text_frame(frame_payload())
    result = await handler.handle_text_frame(frame_payload(id=2, capture_mono_ms=2_000))
    assert result["_action"] == "close"
    assert result["code"] == 4400
    assert result["response"]["code"] == "protocol_violation"


async def test_malformed_header_sheds_the_frame_without_killing_the_session(handler):
    # A single bad header must cost one frame, not the walk. The paired JPEG is discarded so the
    # next header still binds cleanly and assistance keeps running.
    result = await handler.handle_text_frame(frame_payload(pitch_cdeg="not-a-number"))
    assert result == {"type": "error", "code": "invalid_frame_header"}
    assert handler.discard_next_binary is True

    assert await handler.handle_binary_frame(b"orphaned") == {"_action": "continue"}
    assert handler.discard_next_binary is False
    assert await handler.handle_text_frame(frame_payload(id=2, capture_mono_ms=2_000)) is None


async def test_non_monotonic_capture_is_rejected(handler):
    handler.state.last_capture_mono_ms = 5_000
    result = await handler.handle_text_frame(frame_payload(capture_mono_ms=4_000))
    assert result == {"type": "error", "code": "non_monotonic_capture"}
    assert handler.discard_next_binary is True


async def test_frames_inside_the_minimum_interval_are_rate_limited(handler):
    handler.state.last_capture_mono_ms = 1_000
    result = await handler.handle_text_frame(frame_payload(capture_mono_ms=1_020))
    assert result == {"type": "error", "code": "frame_rate_limited"}
    assert handler.discard_next_binary is True


async def test_priority_frames_bypass_the_minimum_interval(handler):
    handler.state.last_capture_mono_ms = 1_000
    assert (
        await handler.handle_text_frame(frame_payload(capture_mono_ms=1_020, priority=True))
        is None
    )


async def test_rejected_frame_does_not_advance_the_freshness_budget(handler):
    handler.state.last_capture_mono_ms = 1_000
    await handler.handle_text_frame(frame_payload(capture_mono_ms=1_020))
    # The rejected frame must not consume the next valid frame's monotonic budget.
    assert handler.state.last_capture_mono_ms == 1_000


async def test_exhausted_rate_budget_rejects_the_frame(make_handler, handler_settings):
    # With REDIS_URL set the shared Redis limiter is the admission authority, not the local
    # bucket — that is the deployed configuration, so it is the one worth asserting on.
    handler = make_handler(
        settings=dataclasses.replace(handler_settings, redis_url="redis://localhost:6379/0"),
        rate_allowed=False,
    )
    result = await handler.handle_text_frame(frame_payload())
    assert result == {"type": "error", "code": "frame_rate_limited"}
    assert handler.discard_next_binary is True


async def test_local_bucket_is_the_admission_authority_without_redis(make_handler):
    # Without REDIS_URL the in-memory bucket decides, and the Redis limiter is never consulted.
    handler = make_handler(rate_allowed=False)
    handler.local_limiter = FrameRateLimiter(0.0, 0.0)
    result = await handler.handle_text_frame(frame_payload())
    assert result == {"type": "error", "code": "frame_rate_limited"}
    handler.device_rate_limiter.allow.assert_not_awaited()


async def test_admission_control_failure_closes_rather_than_silently_admitting(
    make_handler, handler_settings
):
    # Redis down must not degrade into unbounded admission, and must not fail quietly: the phone
    # is told the vision lane is unavailable so it can announce rather than going silent mid-walk.
    handler = make_handler(
        settings=dataclasses.replace(handler_settings, redis_url="redis://localhost:6379/0"),
        limiter_error=RuntimeError("Redis down"),
    )
    result = await handler.handle_text_frame(frame_payload())
    assert result["_action"] == "close"
    assert result["code"] == 1011
    assert result["response"]["code"] == "vision_unavailable"


# ---------------------------------------------------------------------------
# Binary payloads
# ---------------------------------------------------------------------------


async def test_binary_without_pending_header_is_a_protocol_violation(handler):
    result = await handler.handle_binary_frame(jpeg_of(640, 480))
    assert result["_action"] == "close"
    assert result["code"] == 4400
    assert result["response"]["code"] == "protocol_violation"


async def test_binary_size_disagreeing_with_the_header_is_rejected(handler):
    jpeg = jpeg_of(640, 480)
    handler.pending_header = header(jpeg_bytes=len(jpeg) + 1)
    assert await handler.handle_binary_frame(jpeg) == {
        "type": "error",
        "code": "invalid_image_size",
    }


async def test_binary_over_the_byte_cap_is_rejected(make_handler, handler_settings):
    jpeg = jpeg_of(640, 480)
    handler = make_handler(
        settings=dataclasses.replace(handler_settings, max_image_bytes=len(jpeg) - 1)
    )
    handler.pending_header = header(jpeg_bytes=len(jpeg))
    assert await handler.handle_binary_frame(jpeg) == {
        "type": "error",
        "code": "invalid_image_size",
    }


async def test_frame_larger_than_the_supported_side_is_rejected(handler):
    jpeg = jpeg_of(640, 480)
    handler.pending_header = header(jpeg_bytes=len(jpeg), width=2000, height=2000)
    assert await handler.handle_binary_frame(jpeg) == {
        "type": "error",
        "code": "unsupported_frame_size",
    }


async def test_undecodable_payload_is_rejected(handler):
    payload = b"not-a-jpeg-image-content"
    handler.pending_header = header(jpeg_bytes=len(payload))
    assert await handler.handle_binary_frame(payload) == {"type": "error", "code": "invalid_jpeg"}


async def test_jpeg_dimensions_must_match_the_header(handler):
    jpeg = jpeg_of(100, 100)
    handler.pending_header = header(jpeg_bytes=len(jpeg), width=640, height=480)
    assert await handler.handle_binary_frame(jpeg) == {
        "type": "error",
        "code": "jpeg_dimension_mismatch",
    }


async def test_matching_header_and_payload_are_handed_off_for_analysis(handler):
    jpeg = jpeg_of(640, 480)
    assert await handler.handle_text_frame(frame_payload(jpeg_bytes=len(jpeg))) is None

    result = await handler.handle_binary_frame(jpeg)
    assert result["_action"] == "analyze"
    assert result["header"].frame_id == 1
    assert result["decode_ms"] >= 0
    # The slot is released so the next header can bind.
    assert handler.pending_header is None
