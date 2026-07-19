import os
from dataclasses import dataclass


def _env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    jwt_secret: str
    detector: str
    yolo_weights: str
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
    remote_worker_secret: str
    remote_inference_timeout_ms: int

    @classmethod
    def from_env(cls):
        settings = cls(
            environment=os.getenv("AKSHRAVA_ENV", "development").lower(),
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./akshrava.db"),
            jwt_secret=os.getenv("JWT_SECRET", "change-me-before-field-use"),
            detector=os.getenv("DETECTOR", "noop"),
            yolo_weights=os.getenv("YOLO_WEIGHTS", "yolo11s.pt"),
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
            remote_inference_url=os.getenv("REMOTE_INFERENCE_URL", "").rstrip("/"),
            remote_worker_secret=os.getenv("REMOTE_WORKER_SECRET", ""),
            remote_inference_timeout_ms=int(os.getenv("REMOTE_INFERENCE_TIMEOUT_MS", "450")),
        )
        if settings.environment not in {"development", "pilot", "production"}:
            raise ValueError("AKSHRAVA_ENV must be development, pilot or production")
        if settings.environment != "development" and settings.dev_auth_bypass:
            raise ValueError("DEV_AUTH_BYPASS is permitted only when AKSHRAVA_ENV=development")
        if not settings.dev_auth_bypass and settings.jwt_secret == "change-me-before-field-use":
            raise ValueError("JWT_SECRET must be set when DEV_AUTH_BYPASS is false")
        if not settings.dev_auth_bypass and len(settings.jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters when DEV_AUTH_BYPASS is false")
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
        if settings.detector == "remote":
            if not settings.remote_inference_url.startswith(("http://", "https://")):
                raise ValueError("REMOTE_INFERENCE_URL must be an http(s) URL when DETECTOR=remote")
            if len(settings.remote_worker_secret) < 32:
                raise ValueError("REMOTE_WORKER_SECRET must be at least 32 characters when DETECTOR=remote")
        if not 50 <= settings.remote_inference_timeout_ms <= 10_000:
            raise ValueError("REMOTE_INFERENCE_TIMEOUT_MS must be between 50 and 10000")
        return settings
