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
    # Raw-frame diagnostic upload has no in-repo face/plate blur; Important Architecture.md (privacy) requires
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


def test_config_additional_validations(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")

    # Invalid AKSHRAVA_ENV
    with monkeypatch.context() as m:
        m.setenv("AKSHRAVA_ENV", "invalid_env")
        with pytest.raises(ValueError, match="AKSHRAVA_ENV must be"):
            Settings.from_env()

    # Invalid JWT_ALGORITHM
    with monkeypatch.context() as m:
        m.setenv("JWT_ALGORITHM", "ES256")
        with pytest.raises(ValueError, match="JWT_ALGORITHM must be HS256 or RS256"):
            Settings.from_env()

    # HS256 default secret when bypass false
    with monkeypatch.context() as m:
        m.setenv("DEV_AUTH_BYPASS", "false")
        m.setenv("JWT_ALGORITHM", "HS256")
        m.setenv("JWT_SECRET", "change-me-before-field-use")
        with pytest.raises(ValueError, match="JWT_SECRET must be set"):
            Settings.from_env()

    # HS256 short secret when bypass false
    with monkeypatch.context() as m:
        m.setenv("DEV_AUTH_BYPASS", "false")
        m.setenv("JWT_ALGORITHM", "HS256")
        m.setenv("JWT_SECRET", "short")
        with pytest.raises(ValueError, match="at least 32 characters"):
            Settings.from_env()

    # RS256 missing public key file
    with monkeypatch.context() as m:
        m.setenv("JWT_ALGORITHM", "RS256")
        m.delenv("JWT_PUBLIC_KEY_FILE", raising=False)
        with pytest.raises(ValueError, match="JWT_PUBLIC_KEY_FILE is required"):
            Settings.from_env()

    # Invalid MAX_ACTIVE_SESSIONS
    with monkeypatch.context() as m:
        m.setenv("MAX_ACTIVE_SESSIONS", "0")
        with pytest.raises(ValueError, match="MAX_ACTIVE_SESSIONS"):
            Settings.from_env()

    # Invalid ALERT_MAX_AGE_MS
    with monkeypatch.context() as m:
        m.setenv("ALERT_MAX_AGE_MS", "0")
        with pytest.raises(ValueError, match="ALERT_MAX_AGE_MS"):
            Settings.from_env()

    # Invalid MIN_FRAME_INTERVAL_MS
    with monkeypatch.context() as m:
        m.setenv("MIN_FRAME_INTERVAL_MS", "-1")
        with pytest.raises(ValueError, match="MIN_FRAME_INTERVAL_MS"):
            Settings.from_env()

    # Invalid ALERT_RETENTION_DAYS
    with monkeypatch.context() as m:
        m.setenv("ALERT_RETENTION_DAYS", "0")
        with pytest.raises(ValueError, match="ALERT_RETENTION_DAYS"):
            Settings.from_env()

    # Invalid CLOUD_FALLBACK_PROVIDER
    with monkeypatch.context() as m:
        m.setenv("CLOUD_FALLBACK_PROVIDER", "invalid")
        with pytest.raises(ValueError, match="CLOUD_FALLBACK_PROVIDER"):
            Settings.from_env()

    # Invalid CLOUD_MIN_CONFIDENCE
    with monkeypatch.context() as m:
        m.setenv("CLOUD_MIN_CONFIDENCE", "1.5")
        with pytest.raises(ValueError, match="CLOUD_MIN_CONFIDENCE"):
            Settings.from_env()

    # Invalid DETECTOR
    with monkeypatch.context() as m:
        m.setenv("DETECTOR", "invalid_det")
        with pytest.raises(ValueError, match="DETECTOR"):
            Settings.from_env()

    # Registry JSON invalid json
    with monkeypatch.context() as m:
        m.setenv("DETECTOR", "remote")
        m.setenv("REMOTE_WORKER_SECRET", "s" * 32)
        m.setenv("REMOTE_INFERENCE_REGISTRY_JSON", "not-json")
        with pytest.raises(ValueError, match="REMOTE_INFERENCE_REGISTRY_JSON must be valid JSON"):
            Settings.from_env()

    # Registry JSON not list
    with monkeypatch.context() as m:
        m.setenv("DETECTOR", "remote")
        m.setenv("REMOTE_WORKER_SECRET", "s" * 32)
        m.setenv("REMOTE_INFERENCE_REGISTRY_JSON", "{}")
        with pytest.raises(ValueError, match="REMOTE_INFERENCE_REGISTRY_JSON must be a list"):
            Settings.from_env()

    # Remote worker secret short
    with monkeypatch.context() as m:
        m.setenv("DETECTOR", "remote")
        m.setenv("REMOTE_INFERENCE_URL", "http://127.0.0.1:8090")
        m.setenv("REMOTE_WORKER_SECRET", "short")
        with pytest.raises(ValueError, match="REMOTE_WORKER_SECRET must be at least 32 characters"):
            Settings.from_env()

    # Invalid REMOTE_INFERENCE_TIMEOUT_MS
    with monkeypatch.context() as m:
        m.setenv("REMOTE_INFERENCE_TIMEOUT_MS", "10")
        with pytest.raises(ValueError, match="REMOTE_INFERENCE_TIMEOUT_MS"):
            Settings.from_env()

    # Invalid READY_TIMEOUT_MS
    with monkeypatch.context() as m:
        m.setenv("READY_TIMEOUT_MS", "10")
        with pytest.raises(ValueError, match="READY_TIMEOUT_MS"):
            Settings.from_env()

    # Invalid INFERENCE_TIMEOUT_MS
    with monkeypatch.context() as m:
        m.setenv("INFERENCE_TIMEOUT_MS", "10")
        with pytest.raises(ValueError, match="INFERENCE_TIMEOUT_MS"):
            Settings.from_env()

    # Invalid INFERENCE_EXECUTOR_WORKERS
    with monkeypatch.context() as m:
        m.setenv("INFERENCE_EXECUTOR_WORKERS", "0")
        with pytest.raises(ValueError, match="INFERENCE_EXECUTOR_WORKERS"):
            Settings.from_env()

    # Pilot missing schema revision
    with monkeypatch.context() as m:
        _pilot_rs256(m)
        m.setenv("DATABASE_SCHEMA_REVISION", "")
        with pytest.raises(ValueError, match="DATABASE_SCHEMA_REVISION is required"):
            Settings.from_env()

    # Pilot missing metrics scrape token
    with monkeypatch.context() as m:
        _pilot_rs256(m)
        m.delenv("METRICS_SCRAPE_TOKEN", raising=False)
        with pytest.raises(ValueError, match="METRICS_SCRAPE_TOKEN is required"):
            Settings.from_env()

