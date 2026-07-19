from typing import Optional

import jwt

from .config import Settings


class AuthError(ValueError):
    pass


def device_id_from_token(token: Optional[str], settings: Settings) -> str:
    if settings.dev_auth_bypass and token == "dev-device-token":
        return "dev-device"
    if not token:
        raise AuthError("missing device token")
    try:
        # PyJWT only validates exp/iat/aud when the claim is PRESENT in the token. A token
        # minted without an exp claim (e.g. a future minting path that forgets --days) would
        # otherwise be valid forever. Require exp/sub/aud explicitly so the server enforces
        # expiry rather than trusting every caller of the mint script to set it.
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience="akshrava-device",
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("invalid device token") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthError("token missing subject")
    return subject
