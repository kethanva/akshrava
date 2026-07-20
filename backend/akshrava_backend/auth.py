from typing import NamedTuple, Optional
from pathlib import Path

import jwt

from .config import Settings


class AuthError(ValueError):
    pass


class DeviceClaims(NamedTuple):
    device_id: str
    diagnostic_consent: bool


# (path, mtime_ns) -> PEM text. Reading the RS256 public key from disk on every single token
# verification is wasteful under load; cache it keyed on mtime so a rotated key is still picked
# up without a restart the moment the file changes, but a hot path does no repeated file I/O.
_KEY_CACHE: dict = {}


def _verification_key(settings: Settings) -> str:
    if settings.jwt_algorithm == "HS256":
        return settings.jwt_secret
    path = Path(settings.jwt_public_key_file)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as exc:
        raise AuthError("device verification key unavailable") from exc
    cached = _KEY_CACHE.get(path)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    try:
        pem = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthError("device verification key unavailable") from exc
    _KEY_CACHE[path] = (mtime_ns, pem)
    return pem


def device_claims_from_token(token: Optional[str], settings: Settings) -> DeviceClaims:
    """Decode a device JWT. Diagnostic upload consent is a server-side claim, not a query param."""
    if settings.dev_auth_bypass and token == "dev-device-token":
        return DeviceClaims(device_id="dev-device", diagnostic_consent=False)
    if not token:
        raise AuthError("missing device token")
    try:
        # PyJWT only validates exp/iat/aud when the claim is PRESENT in the token. A token
        # minted without an exp claim (e.g. a future minting path that forgets --days) would
        # otherwise be valid forever. Require exp/sub/aud explicitly so the server enforces
        # expiry rather than trusting every caller of the mint script to set it.
        claims = jwt.decode(
            token,
            _verification_key(settings),
            algorithms=[settings.jwt_algorithm],
            audience="akshrava-device",
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("invalid device token") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthError("token missing subject")
    consent = claims.get("diagnostic_consent", False)
    if not isinstance(consent, bool):
        consent = False
    return DeviceClaims(device_id=subject, diagnostic_consent=consent)


def device_id_from_token(token: Optional[str], settings: Settings) -> str:
    return device_claims_from_token(token, settings).device_id
