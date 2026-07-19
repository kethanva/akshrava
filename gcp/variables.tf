variable "project_id" {
  type        = string
  description = "GCP project ID where Akshrava resources are created."
}

variable "credentials_file" {
  type        = string
  default     = ""
  description = "Optional path to a GCP service-account JSON. Empty uses Application Default Credentials."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Region for regional resources (Cloud Run, SQL, Redis, Artifact Registry)."
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "Zone for the GPU worker VM."
}

variable "environment" {
  type        = string
  default     = "pilot"
  description = "AKSHRAVA_ENV for the API and worker (pilot or production)."

  validation {
    condition     = contains(["pilot", "production"], var.environment)
    error_message = "environment must be pilot or production."
  }
}

variable "detector" {
  type        = string
  default     = "noop"
  description = "DETECTOR for the API. Use noop until licensed weights + SHA are ready; then remote."

  validation {
    condition     = contains(["noop", "remote"], var.detector)
    error_message = "detector must be noop or remote for GCP (ultralytics stays on the GPU worker image)."
  }
}

variable "api_image" {
  type        = string
  default     = ""
  description = "Optional override for the API image. Defaults to Artifact Registry akshrava-api:latest."
}

variable "worker_image" {
  type        = string
  default     = ""
  description = "Optional override for the GPU worker image. Defaults to Artifact Registry akshrava-worker:latest."
}

variable "yolo_weights_sha256" {
  type        = string
  default     = ""
  description = "Pinned SHA-256 of YOLO weights on the worker. Required when detector=remote."
}

variable "database_schema_revision" {
  type        = string
  default     = "20260719_01"
  description = "Must match backend Settings expected_schema_revision / alembic head marker."
}

check "remote_requires_weights_sha" {
  assert {
    condition     = var.detector != "remote" || length(var.yolo_weights_sha256) == 64
    error_message = "detector=remote requires yolo_weights_sha256 (64 hex chars)."
  }
}
