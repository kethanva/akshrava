variable "project_id" {
  type        = string
  description = "GCP project ID where Akshrava resources are created."
}

variable "credentials_file" {
  type        = string
  description = "Path to the GCP Service Account JSON credentials file."
  default     = ""
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
