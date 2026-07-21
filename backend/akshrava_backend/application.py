"""Application use-cases called by transport adapters.

FastAPI/WebSocket code owns protocol framing and connection lifetime only.  This coordinator owns
the business transaction that binds a validated frame to calibration state, inference, and audit
storage, making it reusable from a future WebTransport or broker gateway.
"""

import hashlib
import logging

from .protocol import SUPPORTED_LANGUAGES
from .domain import FrameHeader, SessionState

logger = logging.getLogger(__name__)

class SessionApplicationService:
    def __init__(self, store, vision):
        self.store = store
        self.vision = vision

    async def analyze_frame(self, state: SessionState, header: FrameHeader, jpeg: bytes):
        if state.calibration_id != header.calibration_id:
            state.calibration_id = header.calibration_id
            await self.store.upsert_device(state.device_id, header.calibration_id)
            state.geometry_profile = await self.store.geometry_profile(header.calibration_id)
        # Language is a per-device provisioning setting (plan §6.2), not a fleet-wide server
        # default. The phone sends it with every frame header; cache the last valid value on
        # the session so a malformed/absent value on one frame never erases a good prior one.
        if header.language in SUPPORTED_LANGUAGES:
            state.language = header.language
        
        if header.debug_telemetry:
            logger.info("Debug telemetry frame received (id=%s, priority=%s, device=%s)", 
                        header.frame_id, header.priority, state.device_id)

        state.last_capture_mono_ms = header.capture_mono_ms
        result = await self.vision.analyze(state, header, jpeg)
        # Correlates phone/API/GPU logs without exposing a device ID in telemetry or results.
        material = "%s:%s:%s" % (state.trace_prefix, header.frame_id, header.capture_mono_ms)
        result["trace_id"] = header.trace_id or hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        return result

    async def close_session(self, state: SessionState) -> None:
        release = getattr(self.vision, "release_session", None)
        if release is not None:
            await release(state.session_key or state.device_id)
