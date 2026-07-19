import pytest

from akshrava_backend.application import SessionApplicationService
from akshrava_backend.domain import FrameHeader, SessionState


class Store:
    def __init__(self):
        self.upserts = []

    async def upsert_device(self, device_id, calibration_id):
        self.upserts.append((device_id, calibration_id))

    async def geometry_profile(self, calibration_id):
        return {"calibration_id": calibration_id}


class Vision:
    def __init__(self):
        self.received = []
        self.closed = []

    async def analyze(self, state, header, jpeg):
        self.received.append((state, header, jpeg))
        return {"type": "result"}

    async def release_session(self, device_id):
        self.closed.append(device_id)


def header(calibration_id="r0"):
    return FrameHeader(1, 100, None, 1, 1, 1, calibration_id, None, None, None, "normal")


@pytest.mark.asyncio
async def test_session_application_owns_calibration_and_vision_transaction():
    store, vision = Store(), Vision()
    state = SessionState(device_id="phone")
    app = SessionApplicationService(store, vision)
    result = await app.analyze_frame(state, header(), b"jpeg")
    assert result["type"] == "result"
    assert len(result["trace_id"]) == 20
    assert store.upserts == [("phone", "r0")]
    assert state.geometry_profile == {"calibration_id": "r0"}
    assert state.last_capture_mono_ms == 100
    await app.close_session(state)
    assert vision.closed == ["phone"]


@pytest.mark.asyncio
async def test_phone_trace_id_is_preserved_for_cross_tier_correlation():
    store, vision = Store(), Vision()
    result = await SessionApplicationService(store, vision).analyze_frame(
        SessionState(device_id="phone"),
        FrameHeader(1, 100, None, 1, 1, 1, "r0", None, None, None, "normal", trace_id="frame-1-100"),
        b"jpeg",
    )
    assert result["trace_id"] == "frame-1-100"


@pytest.mark.asyncio
async def test_session_language_is_cached_from_the_devices_own_header():
    # Regression test: language is a per-device provisioning setting (plan §6.2), not a
    # fleet-wide server default. A frame carrying a valid language must update the session so
    # VisionService renders speech in the phone's own provisioned language.
    store, vision = Store(), Vision()
    app = SessionApplicationService(store, vision)
    state = SessionState(device_id="phone")
    await app.analyze_frame(
        state,
        FrameHeader(1, 100, None, 1, 1, 1, "r0", None, None, None, "normal", language="hi"),
        b"jpeg",
    )
    assert state.language == "hi"


@pytest.mark.asyncio
async def test_unsupported_language_never_overwrites_a_prior_valid_one():
    store, vision = Store(), Vision()
    app = SessionApplicationService(store, vision)
    state = SessionState(device_id="phone", language="hi")
    await app.analyze_frame(
        state,
        FrameHeader(2, 200, None, 1, 1, 1, "r0", None, None, None, "normal", language="xx"),
        b"jpeg",
    )
    assert state.language == "hi"
