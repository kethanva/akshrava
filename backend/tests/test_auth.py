from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from akshrava_backend.auth import device_claims_from_token, device_id_from_token
from akshrava_backend.config import Settings


def test_rs256_device_token_verifies_with_api_public_key_only(tmp_path, monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_path = tmp_path / "device-public.pem"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("AKSHRAVA_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.setenv("REDIS_URL", "rediss://redis.internal:6380/0")
    monkeypatch.setenv("METRICS_SCRAPE_TOKEN", "test-metrics-token")
    token = jwt.encode(
        {
            "sub": "phone-1",
            "aud": "akshrava-device",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
    )
    assert device_id_from_token(token, Settings.from_env()) == "phone-1"


def test_diagnostic_consent_claim_defaults_false_and_honors_true(tmp_path, monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_path = tmp_path / "device-public.pem"
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    monkeypatch.setenv("AKSHRAVA_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.setenv("REDIS_URL", "rediss://redis.internal:6380/0")
    monkeypatch.setenv("METRICS_SCRAPE_TOKEN", "test-metrics-token")
    settings = Settings.from_env()
    denied = jwt.encode(
        {
            "sub": "phone-1",
            "aud": "akshrava-device",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
    )
    allowed = jwt.encode(
        {
            "sub": "phone-1",
            "aud": "akshrava-device",
            "diagnostic_consent": True,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
    )
    assert device_claims_from_token(denied, settings).diagnostic_consent is False
    assert device_claims_from_token(allowed, settings).diagnostic_consent is True


def _rs256_env(tmp_path, monkeypatch, public_pem: bytes):
    public_path = tmp_path / "device-public.pem"
    public_path.write_bytes(public_pem)
    monkeypatch.setenv("AKSHRAVA_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.setenv("REDIS_URL", "rediss://redis.internal:6380/0")
    monkeypatch.setenv("METRICS_SCRAPE_TOKEN", "test-metrics-token")
    return public_path


def _public_pem(private):
    return private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _rs256_token(private, sub="phone-1"):
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(
        {"sub": sub, "aud": "akshrava-device", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        private_pem,
        algorithm="RS256",
    )


def test_rs256_key_cache_picks_up_a_rotated_public_key_on_mtime_change(tmp_path, monkeypatch):
    import os

    from akshrava_backend import auth

    auth._KEY_CACHE.clear()
    key_a = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = _rs256_env(tmp_path, monkeypatch, _public_pem(key_a))
    settings = Settings.from_env()

    # First verify populates the cache; a token from key A verifies.
    assert device_id_from_token(_rs256_token(key_a), settings) == "phone-1"

    # Rotate: overwrite the key file with a new keypair and bump mtime.
    key_b = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.write_bytes(_public_pem(key_b))
    os.utime(path, ns=(0, 0))  # force a distinct mtime so the cache invalidates

    from akshrava_backend.auth import AuthError

    # The old key's token must now fail (cache invalidated), the new key's token must pass.
    try:
        device_id_from_token(_rs256_token(key_a), settings)
        assert False, "rotated-out key should no longer verify"
    except AuthError:
        pass
    assert device_id_from_token(_rs256_token(key_b), settings) == "phone-1"


def test_rs256_dual_key_accepts_previous_during_rotation_cutover(tmp_path, monkeypatch):
    """During rotate_jwt_rs256.sh cutover, tokens minted with the previous private key still verify."""
    from akshrava_backend import auth

    auth._KEY_CACHE.clear()
    previous = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    current = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    current_path = tmp_path / "current.pem"
    previous_path = tmp_path / "previous.pem"
    current_path.write_bytes(_public_pem(current))
    previous_path.write_bytes(_public_pem(previous))
    monkeypatch.setenv("AKSHRAVA_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", str(current_path))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PREVIOUS_FILE", str(previous_path))
    monkeypatch.setenv("REDIS_URL", "rediss://redis.internal:6380/0")
    monkeypatch.setenv("METRICS_SCRAPE_TOKEN", "test-metrics-token")
    settings = Settings.from_env()
    assert device_id_from_token(_rs256_token(current), settings) == "phone-1"
    assert device_id_from_token(_rs256_token(previous), settings) == "phone-1"


def test_auth_error_branches(tmp_path, monkeypatch):
    from akshrava_backend.auth import AuthError

    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    settings_hs = Settings.from_env()
    with pytest.raises(AuthError, match="missing device token"):
        device_claims_from_token("", settings_hs)

    # 2. Missing public key file (OSError)
    monkeypatch.setenv("AKSHRAVA_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", str(tmp_path / "nonexistent.pem"))
    monkeypatch.setenv("REDIS_URL", "rediss://redis.internal:6380/0")
    monkeypatch.setenv("METRICS_SCRAPE_TOKEN", "test-metrics-token")
    settings_rs = Settings.from_env()

    with pytest.raises(AuthError, match="device verification key unavailable"):
        device_claims_from_token("token", settings_rs)

    # 3. Missing previous key file (non-fatal)
    current = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    current_path = tmp_path / "current.pem"
    current_path.write_bytes(_public_pem(current))
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", str(current_path))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PREVIOUS_FILE", str(tmp_path / "missing_prev.pem"))
    settings_prev_missing = Settings.from_env()

    # Valid token still verifies
    assert device_id_from_token(_rs256_token(current), settings_prev_missing) == "phone-1"

    # 4a. A token with no `sub` claim at all is rejected during decode by PyJWT's require list,
    # so it surfaces as the generic decode failure, not the explicit subject check.
    token_no_sub = jwt.encode(
        {"aud": "akshrava-device", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings_hs.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="invalid device token"):
        device_claims_from_token(token_no_sub, settings_hs)

    # 4b. A present-but-empty subject passes PyJWT's require check, so the explicit subject
    # validation is what stops it — an empty device id would otherwise scope a session to "".
    token_blank_sub = jwt.encode(
        {"sub": "", "aud": "akshrava-device", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings_hs.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="token missing subject"):
        device_claims_from_token(token_blank_sub, settings_hs)

    token_long_sub = jwt.encode(
        {
            "sub": "d" * 129,
            "aud": "akshrava-device",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings_hs.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthError, match="subject is too long"):
        device_claims_from_token(token_long_sub, settings_hs)

    # 5. Non-boolean consent
    token_int_consent = jwt.encode(
        {"sub": "phone-1", "aud": "akshrava-device", "diagnostic_consent": 123, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings_hs.jwt_secret,
        algorithm="HS256",
    )
    claims = device_claims_from_token(token_int_consent, settings_hs)
    assert claims.diagnostic_consent is False
