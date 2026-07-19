import pytest

from akshrava_backend.config import Settings


def test_dev_auth_bypass_is_rejected_for_pilot_environment(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "pilot")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    with pytest.raises(ValueError, match="DEV_AUTH_BYPASS"):
        Settings.from_env()


def test_development_can_explicitly_use_the_local_test_bypass(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    assert Settings.from_env().dev_auth_bypass is True


def test_remote_worker_requires_https_in_pilot(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "pilot")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("DETECTOR", "remote")
    monkeypatch.setenv("REMOTE_INFERENCE_URL", "http://worker.internal/v1/infer")
    monkeypatch.setenv("REMOTE_WORKER_SECRET", "y" * 32)
    with pytest.raises(ValueError, match="HTTPS"):
        Settings.from_env()


def test_development_can_use_private_http_worker(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    monkeypatch.setenv("DETECTOR", "remote")
    monkeypatch.setenv("REMOTE_INFERENCE_URL", "http://127.0.0.1:8000/v1/infer")
    monkeypatch.setenv("REMOTE_WORKER_SECRET", "y" * 32)
    assert Settings.from_env().remote_inference_url.startswith("http://")


def test_production_requires_redis_for_distributed_safety_controls(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", "/run/secrets/jwt/device-public.pem")
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings.from_env()


def test_pilot_remote_inference_requires_mutual_tls_material(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "pilot")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("DETECTOR", "remote")
    monkeypatch.setenv("REMOTE_INFERENCE_URL", "https://worker.internal/v1/infer")
    monkeypatch.setenv("REMOTE_WORKER_SECRET", "y" * 32)
    for name in ("REMOTE_TLS_CA_FILE", "REMOTE_TLS_CLIENT_CERT_FILE", "REMOTE_TLS_CLIENT_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="client certificate"):
        Settings.from_env()
