import json
import logging
import time
from collections import OrderedDict
from json import JSONDecodeError

from .coordination import use_redis_frame_limiter
from .detector import jpeg_dimensions
from .domain import FrameHeader, SessionState
from .protocol import ProtocolError, parse_frame_header
from .rate_limit import FrameRateLimiter

logger = logging.getLogger(__name__)

# Framing limit lives with the framing logic; main.py imports it rather than keeping a copy
# that could silently drift from the value actually enforced here.
MAX_CONTROL_MESSAGE_BYTES = 4096
# A phone normally has one frame in flight. Keep a tiny bounded history so a result acknowledgement
# that races the next frame is accepted, while an arbitrary stream of old ids cannot grow session
# memory or fabricate delivery telemetry.
MAX_PENDING_RESULT_ACKS = 4
# Non-frame text messages skip the frame admission limiter. A ping still renews the admission
# lease in main.py; result_ack deliberately does not. Without an independent bound here, an
# authenticated client could send ping/status/look/unknown messages at line rate and make the
# server allocate responses -- and, for ping, hit Redis -- at the same rate. Four per second with
# an eight-message burst is still comfortably above legitimate traffic (about 1.2 result acks/s
# plus one ping/minute) while keeping the abuse ceiling meaningfully small.
CONTROL_MESSAGE_RATE_PER_SECOND = 4.0
CONTROL_MESSAGE_BURST = 8.0


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

        self.pending_header: FrameHeader | None = None
        self.discard_next_binary = False
        # Malformed JSON may have been a frame header whose JPEG is still on the wire.
        # Discard that orphan binary instead of 4400; a following ping/header clears this.
        self.orphan_binary = False
        self._control_message_limiter = FrameRateLimiter(
            CONTROL_MESSAGE_RATE_PER_SECOND, CONTROL_MESSAGE_BURST
        )
        self._pending_result_acks: OrderedDict[int, bool] = OrderedDict()
        # Old phones do not send result acknowledgements. The field is advertised by new phones
        # on ordinary frame headers, which older servers safely ignore, so rollout does not turn
        # legacy clients into a false "missing delivery" operational incident.
        self.result_acknowledgement_supported = False

    def note_result_sent(self, frame_id: int, ack_expected: bool) -> None:
        """Allow exactly one later acknowledgement for a result about to be sent to the socket.

        This registration must precede ``send_json``: the receive loop runs independently, and a
        handset can acknowledge while the send awaits transport. The caller removes the entry if
        the write fails. The phone's acknowledgement is useful only when it corresponds to a
        result this session intended to send, and this small bounded set also handles a delayed
        ack after the next frame starts.
        """
        # A legacy phone that did not negotiate acknowledgements must not get a pending slot.
        # Otherwise an unsolicited result_ack from that phone would be accepted and inflate the
        # acknowledged numerator even though this result was never part of the expectation set.
        if not ack_expected:
            return
        self._pending_result_acks[frame_id] = True
        self._pending_result_acks.move_to_end(frame_id)
        while len(self._pending_result_acks) > MAX_PENDING_RESULT_ACKS:
            # Evicting a slot is the exact moment a result becomes provably unacknowledged: the
            # phone had this result and MAX_PENDING_RESULT_ACKS further results' worth of time to
            # acknowledge it, and did not. Counting here (rather than deriving
            # expected-minus-acknowledged over an export window) is what makes the delivery alert
            # trustworthy -- a frame still legitimately in flight is never counted as missing.
            # Only meaningful for phones that advertised acknowledgement support; an old client
            # that never acks must not generate a fleet-wide delivery alarm.
            _evicted_frame_id, evicted_ack_expected = self._pending_result_acks.popitem(last=False)
            if evicted_ack_expected:
                self.metrics.phone_result_unacknowledged()

    def forget_result_sent(self, frame_id: int) -> None:
        """Discard a pre-send acknowledgement slot after its result write failed."""
        self._pending_result_acks.pop(frame_id, None)

    def record_unacknowledged_on_close(self) -> None:
        """Settle acknowledgement slots that can no longer receive a reply.

        Eviction catches sustained misses, but without this close-time drain the final four
        results of every session could disappear without an acknowledgement and never reach the
        delivery-failure metric. At socket teardown they are no longer merely in flight: this
        authenticated session can never acknowledge them.
        """
        pending = len(self._pending_result_acks)
        self._pending_result_acks.clear()
        for _ in range(pending):
            self.metrics.phone_result_unacknowledged()

    async def handle_text_frame(self, raw_payload: str) -> dict | None:
        """Verify the header text framing and apply admission rate limits.

        Returns an action dictionary, error response dictionary, or None (waiting for binary).
        """
        if len(raw_payload.encode("utf-8")) > MAX_CONTROL_MESSAGE_BYTES:
            raise ProtocolError("control message is too large")
        try:
            payload = json.loads(raw_payload)
        except JSONDecodeError:
            # One corrupted control frame is a transport artefact, not a misbehaving client.
            # Closing the socket for it cost the user a full reconnect and a spoken outage.
            logger.warning("discarding malformed control message for device=%s", self.device_id)
            self.metrics.control_message_rejected()
            self.orphan_binary = True
            return {"type": "error", "code": "malformed_control_message"}
        if not isinstance(payload, dict):
            raise ProtocolError("control message must be a JSON object")
        self.orphan_binary = False
        message_type = payload.get("type")

        if message_type != "frame" and not self._control_message_limiter.allow():
            # Silently shed: a starved control stream on a device that is otherwise misbehaving
            # must not itself become a reason to close a walking session's socket.
            logger.warning("control message rate limit exceeded for device=%s", self.device_id)
            return None

        if message_type == "ping":
            if await self.store.is_device_revoked(self.device_id):
                return {
                    "_action": "close",
                    "code": 4403,
                    "response": {"type": "error", "code": "device_revoked", "detail": "Device revoked"},
                }
            return {"type": "pong"}
        elif message_type == "result_ack":
            frame_id = payload.get("frame_id")
            fresh = payload.get("fresh")
            if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
                logger.warning("dropping malformed result acknowledgement: invalid frame_id")
                return {"_action": "result_ack"}
            if not isinstance(fresh, bool):
                logger.warning("dropping malformed result acknowledgement: invalid freshness")
                return {"_action": "result_ack"}
            if frame_id in self._pending_result_acks:
                self._pending_result_acks.pop(frame_id)
                self.metrics.phone_result_acknowledged(fresh=fresh)
            # A duplicate/late acknowledgement is harmless and gets no response: sending an ack
            # of the ack would add control traffic to the one-in-flight result path.
            return {"_action": "result_ack"}
        elif message_type == "frame":
            if await self.store.is_device_revoked(self.device_id):
                # Send a readable signal alongside the close. iOS's public WebSocket API cannot
                # represent a custom application close code outside 1000-1015 -- it collapses any
                # such code to a generic "invalid" value, discarding the number entirely, so a
                # client relying on the close code alone cannot tell "revoked" from an ordinary
                # transport drop and retries forever. The JSON body survives that platform limit.
                return {
                    "_action": "close",
                    "code": 4403,
                    "response": {"type": "error", "code": "device_revoked", "detail": "Device revoked"},
                }

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

            try:
                header = parse_frame_header(payload)
            except ProtocolError as exc:
                # A single malformed frame must not tear down the session. Soft-shed and discard
                # the paired JPEG so the next header can bind cleanly.
                logger.warning(
                    "rejecting malformed frame header for device=%s: %s",
                    self.device_id,
                    exc,
                )
                self.metrics.reject_frame()
                self.discard_next_binary = True
                return {"type": "error", "code": "invalid_frame_header"}

            result_acknowledgement = payload.get("result_acknowledgement", False)
            if not isinstance(result_acknowledgement, bool):
                self.metrics.reject_frame()
                self.discard_next_binary = True
                return {"type": "error", "code": "invalid_frame_header"}
            self.result_acknowledgement_supported = result_acknowledgement

            previous = self.state.last_capture_mono_ms

            if previous is not None and header.capture_mono_ms <= previous:
                self.metrics.reject_frame()
                self.discard_next_binary = True
                return {"type": "error", "code": "non_monotonic_capture"}

            # NOTE: last_capture_mono_ms is updated only when analysis proceeds (in
            # application.py). A frame rejected here (rate-limited, size error, etc.)
            # does NOT advance the counter — intentionally. The monotonic sequence
            # governs successfully accepted frames, so a rejected frame never consumes
            # the freshness budget of the next valid one.

            if header.priority:
                rate_id = f"{self.device_id}:priority"
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
        server_received_epoch_ms = int(time.time() * 1000)
        decode_started = time.monotonic()
        if self.pending_header is None and self.orphan_binary:
            self.orphan_binary = False
            return {"_action": "continue"}
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
            "server_received_epoch_ms": server_received_epoch_ms,
        }
