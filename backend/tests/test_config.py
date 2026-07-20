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


def _pilot_rs256(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "pilot")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", "/run/secrets/jwt/device-public.pem")
    monkeypatch.setenv("METRICS_SCRAPE_TOKEN", "test-metrics-token")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


def test_pilot_rejects_hs256_device_tokens(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "pilot")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    with pytest.raises(ValueError, match="pilot and production require JWT_ALGORITHM=RS256"):
        Settings.from_env()


def test_remote_worker_requires_https_in_pilot(monkeypatch):
    _pilot_rs256(monkeypatch)
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


def test_remote_inference_registry_json_can_supply_endpoints(monkeypatch):
    _pilot_rs256(monkeypatch)
    monkeypatch.setenv("DETECTOR", "remote")
    monkeypatch.delenv("REMOTE_INFERENCE_URL", raising=False)
    monkeypatch.setenv(
        "REMOTE_INFERENCE_REGISTRY_JSON",
        '[{"id":"gpu-a","url":"https://gpu-a.internal/v1/infer"},'
        '{"id":"gpu-b","url":"https://gpu-b.internal/v1/infer","enabled":false}]',
    )
    monkeypatch.setenv("REMOTE_WORKER_SECRET", "y" * 32)
    monkeypatch.setenv("REMOTE_TLS_CA_FILE", "/run/secrets/worker-ca.pem")
    monkeypatch.setenv("REMOTE_TLS_CLIENT_CERT_FILE", "/run/secrets/worker-client.pem")
    monkeypatch.setenv("REMOTE_TLS_CLIENT_KEY_FILE", "/run/secrets/worker-client-key.pem")
    settings = Settings.from_env()
    assert "gpu-a" in settings.remote_inference_registry_json


def test_pilot_ultralytics_requires_model_sha256(monkeypatch):
    _pilot_rs256(monkeypatch)
    monkeypatch.setenv("DETECTOR", "ultralytics")
    monkeypatch.delenv("YOLO_WEIGHTS_SHA256", raising=False)
    with pytest.raises(ValueError, match="YOLO_WEIGHTS_SHA256"):
        Settings.from_env()


def test_production_requires_redis_for_distributed_safety_controls(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "production")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY_FILE", "/run/secrets/jwt/device-public.pem")
    monkeypatch.setenv("METRICS_SCRAPE_TOKEN", "test-metrics-token")
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings.from_env()


def test_pilot_requires_redis_for_distributed_safety_controls(monkeypatch):
    _pilot_rs256(monkeypatch)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings.from_env()


def test_diagnostic_uploads_default_off(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("DIAGNOSTIC_UPLOADS_ENABLED", raising=False)
    assert Settings.from_env().diagnostic_uploads_enabled is False


def test_pilot_remote_inference_requires_mutual_tls_material(monkeypatch):
    _pilot_rs256(monkeypatch)
    monkeypatch.setenv("DETECTOR", "remote")
    monkeypatch.setenv("REMOTE_INFERENCE_URL", "https://worker.internal/v1/infer")
    monkeypatch.setenv("REMOTE_WORKER_SECRET", "y" * 32)
    for name in ("REMOTE_TLS_CA_FILE", "REMOTE_TLS_CLIENT_CERT_FILE", "REMOTE_TLS_CLIENT_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="client certificate"):
        Settings.from_env()


def test_diagnostic_uploads_are_blocked_outside_development_until_blur_exists(monkeypatch):
    # Raw-frame diagnostic upload has no in-repo face/plate blur; PRIVACY.md requires
    # blur-before-upload. A signed consent claim + bucket must NOT be enough to ship unblurred
    # bystander imagery to cloud in pilot/production.
    _pilot_rs256(monkeypatch)
    monkeypatch.setenv("DIAGNOSTIC_UPLOADS_ENABLED", "true")
    monkeypatch.setenv("GCP_DIAGNOSTICS_BUCKET", "akshrava-diagnostics")
    with pytest.raises(ValueError, match="DIAGNOSTIC_UPLOADS_ENABLED is not permitted outside development"):
        Settings.from_env()


def test_diagnostic_uploads_plumbing_is_allowed_in_development(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "development")
    monkeypatch.setenv("DIAGNOSTIC_UPLOADS_ENABLED", "true")
    monkeypatch.setenv("GCP_DIAGNOSTICS_BUCKET", "akshrava-diagnostics")
    assert Settings.from_env().diagnostic_uploads_enabled is True
