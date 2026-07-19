variable "project_id" {
  type        = string
  description = "GCP project ID where Akshrava resources are created."
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
  description = "Digest-pinned API image (preferred). Empty falls back to Artifact Registry :latest for bootstrap only."
}

variable "worker_image" {
  type        = string
  default     = ""
  description = "Digest-pinned GPU worker image (preferred). Empty falls back to :latest for bootstrap only."
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

variable "api_allow_unauthenticated" {
  type        = bool
  default     = false
  description = "If true, grant allUsers run.invoker (discouraged). Prefer api_invoker_members or a private edge."
}

variable "api_invoker_members" {
  type        = list(string)
  default     = []
  description = "IAM members granted roles/run.invoker (e.g. serviceAccount:...@... or group:ops@...). Empty + api_allow_unauthenticated=false means private Cloud Run."
}

variable "redis_transit_encryption" {
  type        = bool
  default     = false
  description = "When true, use Memorystore STANDARD_HA with SERVER_AUTHENTICATION and rediss:// URLs. BASIC AUTH remains redis://."
}

variable "manage_pki_in_terraform" {
  type        = bool
  default     = true
  description = "When true, generate JWT/worker TLS keys in Terraform (lands in state). Prefer false + external PEMs for pilot/production."
}

variable "jwt_public_key_pem" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Required when manage_pki_in_terraform=false."
}

variable "jwt_private_key_pem" {
  type        = string
  default     = ""
  sensitive   = true
  description = "Required when manage_pki_in_terraform=false. Never mount on Cloud Run."
}

variable "worker_ca_cert_pem" {
  type      = string
  default   = ""
  sensitive = true
}

variable "worker_server_cert_pem" {
  type      = string
  default   = ""
  sensitive = true
}

variable "worker_server_key_pem" {
  type      = string
  default   = ""
  sensitive = true
}

variable "worker_client_cert_pem" {
  type      = string
  default   = ""
  sensitive = true
}

variable "worker_client_key_pem" {
  type      = string
  default   = ""
  sensitive = true
}

check "remote_requires_weights_sha" {
  assert {
    condition     = var.detector != "remote" || length(var.yolo_weights_sha256) == 64
    error_message = "detector=remote requires yolo_weights_sha256 (64 hex chars)."
  }
}

check "external_pki_complete" {
  assert {
    condition = var.manage_pki_in_terraform || (
      length(var.jwt_public_key_pem) > 0 &&
      length(var.jwt_private_key_pem) > 0 &&
      length(var.worker_ca_cert_pem) > 0 &&
      length(var.worker_server_cert_pem) > 0 &&
      length(var.worker_server_key_pem) > 0 &&
      length(var.worker_client_cert_pem) > 0 &&
      length(var.worker_client_key_pem) > 0
    )
    error_message = "manage_pki_in_terraform=false requires all jwt_* and worker_* PEM variables."
  }
}
