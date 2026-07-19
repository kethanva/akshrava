import os
from dataclasses import dataclass


def _env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    jwt_secret: str
    jwt_algorithm: str
    jwt_public_key_file: str
    max_active_sessions: int
    detector: str
    yolo_weights: str
    yolo_weights_sha256: str
    max_image_bytes: int
    max_frame_side: int
    dev_auth_bypass: bool
    alert_max_age_ms: int
    min_frame_interval_ms: int
    alert_retention_days: int
    cloud_fallback_provider: str
    cloud_min_confidence: float
    aws_region: str
    azure_vision_endpoint: str
    azure_vision_key: str
    remote_inference_url: str
    remote_inference_registry_json: str
    remote_worker_secret: str
    remote_inference_timeout_ms: int
    ready_timeout_ms: int
    redis_url: str
    inference_timeout_ms: int
    inference_executor_workers: int
    expected_schema_revision: str
    remote_tls_ca_file: str
    remote_tls_client_cert_file: str
    remote_tls_client_key_file: str
    gcp_diagnostics_bucket: str

    @classmethod
    def from_env(cls):
        settings = cls(
            environment=os.getenv("AKSHRAVA_ENV", "development").lower(),
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./akshrava.db"),
            jwt_secret=os.getenv("JWT_SECRET", "change-me-before-field-use"),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256").upper(),
            jwt_public_key_file=os.getenv("JWT_PUBLIC_KEY_FILE", "").strip(),
            max_active_sessions=int(os.getenv("MAX_ACTIVE_SESSIONS", "200")),
            detector=os.getenv("DETECTOR", "noop"),
            yolo_weights=os.getenv("YOLO_WEIGHTS", "yolo11s.pt"),
            yolo_weights_sha256=os.getenv("YOLO_WEIGHTS_SHA256", "").strip().lower(),
            max_image_bytes=int(os.getenv("MAX_IMAGE_BYTES", "200000")),
            max_frame_side=int(os.getenv("MAX_FRAME_SIDE", "1280")),
            dev_auth_bypass=_env_bool("DEV_AUTH_BYPASS", False),
            alert_max_age_ms=int(os.getenv("ALERT_MAX_AGE_MS", "500")),
            min_frame_interval_ms=int(os.getenv("MIN_FRAME_INTERVAL_MS", "200")),
            alert_retention_days=int(os.getenv("ALERT_RETENTION_DAYS", "30")),
            cloud_fallback_provider=os.getenv("CLOUD_FALLBACK_PROVIDER", "none").lower(),
            cloud_min_confidence=float(os.getenv("CLOUD_MIN_CONFIDENCE", "0.55")),
            aws_region=os.getenv("AWS_REGION", ""),
            azure_vision_endpoint=os.getenv("AZURE_VISION_ENDPOINT", ""),
            azure_vision_key=os.getenv("AZURE_VISION_KEY", ""),
            remote_inference_url=os.getenv("REMOTE_INFERENCE_URL", "").strip(),
            remote_inference_registry_json=os.getenv("REMOTE_INFERENCE_REGISTRY_JSON", "").strip(),
            remote_worker_secret=os.getenv("REMOTE_WORKER_SECRET", ""),
            remote_inference_timeout_ms=int(os.getenv("REMOTE_INFERENCE_TIMEOUT_MS", "450")),
            ready_timeout_ms=int(os.getenv("READY_TIMEOUT_MS", "2000")),
            redis_url=os.getenv("REDIS_URL", "").strip(),
            inference_timeout_ms=int(os.getenv("INFERENCE_TIMEOUT_MS", "800")),
            inference_executor_workers=int(os.getenv("INFERENCE_EXECUTOR_WORKERS", "2")),
            expected_schema_revision=os.getenv("DATABASE_SCHEMA_REVISION", "20260719_01").strip(),
            remote_tls_ca_file=os.getenv("REMOTE_TLS_CA_FILE", "").strip(),
            remote_tls_client_cert_file=os.getenv("REMOTE_TLS_CLIENT_CERT_FILE", "").strip(),
            remote_tls_client_key_file=os.getenv("REMOTE_TLS_CLIENT_KEY_FILE", "").strip(),
            gcp_diagnostics_bucket=os.getenv("GCP_DIAGNOSTICS_BUCKET", "").strip(),
        )
        if settings.environment not in {"development", "pilot", "production"}:
            raise ValueError("AKSHRAVA_ENV must be development, pilot or production")
        if settings.environment != "development" and settings.dev_auth_bypass:
            raise ValueError("DEV_AUTH_BYPASS is permitted only when AKSHRAVA_ENV=development")
        if settings.jwt_algorithm not in {"HS256", "RS256"}:
            raise ValueError("JWT_ALGORITHM must be HS256 or RS256")
        if settings.jwt_algorithm == "HS256" and not settings.dev_auth_bypass and settings.jwt_secret == "change-me-before-field-use":
            raise ValueError("JWT_SECRET must be set when DEV_AUTH_BYPASS is false")
        if settings.jwt_algorithm == "HS256" and not settings.dev_auth_bypass and len(settings.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters when DEV_AUTH_BYPASS is false")
        if settings.environment != "development" and settings.jwt_algorithm != "RS256":
            raise ValueError("pilot and production require JWT_ALGORITHM=RS256 with per-device provisioning keys")
        if settings.jwt_algorithm == "RS256" and not settings.jwt_public_key_file:
            raise ValueError("JWT_PUBLIC_KEY_FILE is required when JWT_ALGORITHM=RS256")
        if not 1 <= settings.max_active_sessions <= 100_000:
            raise ValueError("MAX_ACTIVE_SESSIONS must be between 1 and 100000")
        if settings.alert_max_age_ms <= 0:
            raise ValueError("ALERT_MAX_AGE_MS must be positive")
        if settings.min_frame_interval_ms < 0:
            raise ValueError("MIN_FRAME_INTERVAL_MS must be non-negative")
        if not 1 <= settings.alert_retention_days <= 3650:
            raise ValueError("ALERT_RETENTION_DAYS must be between 1 and 3650")
        if settings.cloud_fallback_provider not in {"none", "aws", "gcp", "azure"}:
            raise ValueError("CLOUD_FALLBACK_PROVIDER must be none, aws, gcp or azure")
        if not 0 <= settings.cloud_min_confidence <= 1:
            raise ValueError("CLOUD_MIN_CONFIDENCE must be between 0 and 1")
        if settings.detector not in {"noop", "ultralytics", "remote"}:
            raise ValueError("DETECTOR must be noop, ultralytics or remote")
        if settings.environment != "development" and settings.detector == "ultralytics" and not settings.yolo_weights_sha256:
            raise ValueError("YOLO_WEIGHTS_SHA256 is required when DETECTOR=ultralytics outside development")
        if settings.detector == "remote":
            allowed_schemes = ("http://", "https://") if settings.environment == "development" else ("https://",)
            remote_urls = [url.strip().rstrip("/") for url in settings.remote_inference_url.split(",") if url.strip()]
            if settings.remote_inference_registry_json:
                import json

                try:
                    registry_items = json.loads(settings.remote_inference_registry_json)
                except json.JSONDecodeError as exc:
                    raise ValueError("REMOTE_INFERENCE_REGISTRY_JSON must be valid JSON") from exc
                if not isinstance(registry_items, list):
                    raise ValueError("REMOTE_INFERENCE_REGISTRY_JSON must be a list")
                remote_urls = [
                    str(item.get("url", "")).strip().rstrip("/")
                    for item in registry_items
                    if isinstance(item, dict) and item.get("enabled", True)
                ]
            if not remote_urls or any(not url.startswith(allowed_schemes) for url in remote_urls):
                raise ValueError("remote inference endpoints must use HTTPS outside development when DETECTOR=remote")
            if len(settings.remote_worker_secret) < 32:
                raise ValueError("REMOTE_WORKER_SECRET must be at least 32 characters when DETECTOR=remote")
            if settings.environment != "development" and not all((
                settings.remote_tls_ca_file,
                settings.remote_tls_client_cert_file,
                settings.remote_tls_client_key_file,
            )):
                raise ValueError("remote inference outside development requires CA, client certificate, and client key")
        if not 50 <= settings.remote_inference_timeout_ms <= 10_000:
            raise ValueError("REMOTE_INFERENCE_TIMEOUT_MS must be between 50 and 10000")
        if not 100 <= settings.ready_timeout_ms <= 10_000:
            raise ValueError("READY_TIMEOUT_MS must be between 100 and 10000")
        if settings.environment == "production" and not settings.redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("REDIS_URL is required in production for distributed session and replay controls")
        if not 50 <= settings.inference_timeout_ms <= 10_000:
            raise ValueError("INFERENCE_TIMEOUT_MS must be between 50 and 10000")
        if not 1 <= settings.inference_executor_workers <= 32:
            raise ValueError("INFERENCE_EXECUTOR_WORKERS must be between 1 and 32")
        if settings.environment != "development" and not settings.expected_schema_revision:
            raise ValueError("DATABASE_SCHEMA_REVISION is required outside development")
        return settings
