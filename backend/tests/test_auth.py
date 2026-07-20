from datetime import datetime, timedelta, timezone

import jwt
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
    import akshrava_backend.auth as auth

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
    import akshrava_backend.auth as auth

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
