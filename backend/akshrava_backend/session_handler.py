import json
import logging
import time
from typing import Optional

from .domain import FrameHeader, SessionState
from .rate_limit import FrameRateLimiter
from .coordination import use_redis_frame_limiter
from .protocol import ProtocolError, parse_frame_header
from .detector import jpeg_dimensions

logger = logging.getLogger(__name__)


class FrameStreamHandler:
    def __init__(
        self,
        device_id: str,
        state: SessionState,
        settings,
        store,
        device_rate_limiter,
        metrics,
        local_limiter: FrameRateLimiter,
        priority_local_limiter: FrameRateLimiter,
        normal_rate: float,
        normal_burst: float,
        priority_rate: float,
        priority_burst: float,
    ):
        self.device_id = device_id
        self.state = state
        self.settings = settings
        self.store = store
        self.device_rate_limiter = device_rate_limiter
        self.metrics = metrics
        self.local_limiter = local_limiter
        self.priority_local_limiter = priority_local_limiter
        self.normal_rate = normal_rate
        self.normal_burst = normal_burst
        self.priority_rate = priority_rate
        self.priority_burst = priority_burst

        self.pending_header: Optional[FrameHeader] = None
        self.discard_next_binary = False

    async def handle_text_frame(self, raw_payload: str) -> Optional[dict]:
        """Verify the header text framing and apply admission rate limits.

        Returns an action dictionary, error response dictionary, or None (waiting for binary).
        """
        # 4096 is MAX_CONTROL_MESSAGE_BYTES
        if len(raw_payload.encode("utf-8")) > 4096:
            raise ProtocolError("control message is too large")
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ProtocolError("control message must be a JSON object")
        message_type = payload.get("type")

        if message_type == "ping":
            return {"type": "pong"}
        elif message_type == "frame":
            if await self.store.is_device_revoked(self.device_id):
                return {"_action": "close", "code": 4403}

            if self.pending_header is not None or self.discard_next_binary:
                logger.error("Protocol violation: received header before prior binary payload was resolved")
                return {
                    "_action": "close",
                    "code": 4400,
                    "response": {
                        "type": "error",
                        "code": "protocol_violation",
                        "detail": "Header out of sequence",
                    },
                }

            header = parse_frame_header(payload)
            previous = self.state.last_capture_mono_ms

            if previous is not None and header.capture_mono_ms <= previous:
                self.metrics.reject_frame()
                self.discard_next_binary = True
                return {"type": "error", "code": "non_monotonic_capture"}

            if header.priority:
                rate_id = "%s:priority" % self.device_id
                rate_per_second, burst = self.priority_rate, self.priority_burst
                dev_limiter = self.priority_local_limiter
            else:
                rate_id = self.device_id
                rate_per_second, burst = self.normal_rate, self.normal_burst
                dev_limiter = self.local_limiter

            try:
                rate_allowed = (
                    await self.device_rate_limiter.allow(rate_id, rate_per_second, burst)
                    if use_redis_frame_limiter(redis_url=self.settings.redis_url)
                    else dev_limiter.allow()
                )
            except Exception:
                logger.exception("frame admission control unavailable")
                return {
                    "_action": "close",
                    "code": 1011,
                    "response": {"type": "error", "code": "vision_unavailable"},
                }

            if not rate_allowed or (
                not header.priority
                and previous is not None
                and header.capture_mono_ms - previous < self.settings.min_frame_interval_ms
            ):
                self.metrics.reject_frame()
                self.discard_next_binary = True
                return {"type": "error", "code": "frame_rate_limited"}

            self.pending_header = header
            return None
        elif message_type == "status":
            return {"type": "status_ack"}
        elif message_type == "look":
            return {"type": "look_ack"}
        else:
            return {"type": "error", "code": "unknown_message"}

    async def handle_binary_frame(self, jpeg: bytes) -> dict:
        """Verify binary size, bounds, JPEG dimensions, and match with the pending header."""
        decode_started = time.monotonic()
        if not (self.pending_header is not None or self.discard_next_binary):
            logger.error("Protocol violation: received binary bytes without pending header")
            return {
                "_action": "close",
                "code": 4400,
                "response": {
                    "type": "error",
                    "code": "protocol_violation",
                    "detail": "Binary payload out of sequence",
                },
            }

        if self.discard_next_binary:
            self.discard_next_binary = False
            return {"_action": "continue"}

        header = self.pending_header
        self.pending_header = None

        if len(jpeg) != header.jpeg_bytes or len(jpeg) > self.settings.max_image_bytes:
            self.metrics.reject_frame()
            return {"type": "error", "code": "invalid_image_size"}
        if header.width > self.settings.max_frame_side or header.height > self.settings.max_frame_side:
            self.metrics.reject_frame()
            return {"type": "error", "code": "unsupported_frame_size"}

        try:
            actual_width, actual_height = jpeg_dimensions(jpeg)
        except ValueError:
            self.metrics.reject_frame()
            return {"type": "error", "code": "invalid_jpeg"}

        if (actual_width, actual_height) != (header.width, header.height):
            self.metrics.reject_frame()
            return {"type": "error", "code": "jpeg_dimension_mismatch"}

        return {
            "_action": "analyze",
            "header": header,
            "decode_ms": int((time.monotonic() - decode_started) * 1000),
        }
