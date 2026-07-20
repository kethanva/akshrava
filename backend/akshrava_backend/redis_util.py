"""Shared Redis client construction for redis:// and rediss:// (Memorystore TLS)."""

from __future__ import annotations

import os
from typing import Any, Dict


def _ssl_kwargs_for_url(url: str) -> Dict[str, Any]:
    """SSL kwargs for redis-py asyncio ``from_url``.

    ``ssl_cert_reqs`` must be the strings ``none`` / ``optional`` / ``required``
    (not ``ssl.CERT_*`` enums). Passing an enum leaves RedisSSLContext without
    ``cert_reqs`` and raises AttributeError on connect.
    Memorystore is addressed by private IP, so hostname checks stay off.
    """
    if not url.startswith("rediss://"):
        return {}
    ca_file = os.getenv("REDIS_CA_CERT_FILE", "").strip()
    ca_pem = os.getenv("REDIS_CA_PEM", "").strip()
    if ca_file:
        return {
            "ssl_cert_reqs": "required",
            "ssl_ca_certs": ca_file,
            "ssl_check_hostname": False,
        }
    if ca_pem:
        return {
            "ssl_cert_reqs": "required",
            "ssl_ca_data": ca_pem,
            "ssl_check_hostname": False,
        }
    # No explicit CA: let redis-py use the system trust store rather than disabling
    # verification. A missing CA outside development is a deployment error, not
    # something to silently work around (fail-closed over fail-open).
    return {
        "ssl_check_hostname": False,
    }


def async_redis_from_url(url: str, *, decode_responses: bool = True, **kwargs: Any):
    from redis.asyncio import Redis

    merged = {
        "decode_responses": decode_responses,
        "socket_connect_timeout": kwargs.pop("socket_connect_timeout", 1),
        "socket_timeout": kwargs.pop("socket_timeout", 1),
        **_ssl_kwargs_for_url(url),
        **kwargs,
    }
    return Redis.from_url(url, **merged)
