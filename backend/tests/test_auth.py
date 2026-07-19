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
