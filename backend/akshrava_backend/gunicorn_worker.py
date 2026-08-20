"""Gunicorn/Uvicorn deployment adapter with bounded WebSocket transport buffering.

Application framing rejects images above ``MAX_IMAGE_BYTES``, but that check runs only after the
ASGI server has assembled a complete WebSocket message. Uvicorn otherwise permits a 16 MiB message
and queues many messages per connection, which lets an authenticated or pre-auth peer consume far
more memory than the vision protocol permits before our handler sees a byte.
"""

from typing import ClassVar

from uvicorn.workers import UvicornWorker


class BoundedUvicornWorker(UvicornWorker):
    CONFIG_KWARGS: ClassVar[dict[str, object]] = {
        **UvicornWorker.CONFIG_KWARGS,
        "ws_max_size": 1_048_576,
        "ws_max_queue": 8,
        # JPEG is already compressed and control JSON is tiny. Compression adds CPU/decompression
        # attack surface without a meaningful bandwidth win for this protocol.
        "ws_per_message_deflate": False,
    }
