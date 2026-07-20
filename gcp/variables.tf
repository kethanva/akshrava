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

variable "worker_use_gpu" {
  type        = bool
  default     = true
  description = "When true, provision an L4 GPU worker (requires NVIDIA_L4 quota). When false, use a CPU VM for supervised remote-detector bench (REQUIRE_GPU=false)."
}

variable "worker_machine_type" {
  type        = string
  default     = ""
  description = "Override worker machine type. Empty selects g2-standard-4 (GPU) or n2-standard-8 (CPU) from worker_use_gpu."
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
  default     = false
  description = "When true, generate JWT/worker TLS keys in Terraform (lands in state). Keep false for pilot/production and supply external PEMs."
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

check "phone_wss_reachability" {
  assert {
    condition     = var.api_allow_unauthenticated || length(var.api_invoker_members) > 0
    error_message = "Phones cannot reach private Cloud Run. Set api_invoker_members (edge SA/group) or temporarily api_allow_unauthenticated=true with a documented public edge."
  }
}

check "remote_requires_weights_sha" {
  assert {
    condition     = var.detector != "remote" || length(var.yolo_weights_sha256) == 64
    error_message = "detector=remote requires yolo_weights_sha256 (64 hex chars)."
  }
}

check "cloud_armor_requires_domain" {
  assert {
    condition     = !var.enable_cloud_armor || length(var.cloud_armor_domain) > 0
    error_message = "cloud_armor_domain is required when enable_cloud_armor=true."
  }
}

# Checks on variables removed because local file fallback natively validates existence at plan time.

variable "enable_worker_ha" {
  type        = bool
  default     = false
  description = "When true, replace the single-VM GPU/CPU worker with a regional Managed Instance Group behind an internal L4 LB with auto-healing (see worker_ha.tf). Default false preserves the documented single-VM pilot posture; review a terraform plan before enabling against a live project."
}

variable "worker_ha_target_size" {
  type        = number
  default     = 2
  description = "Worker MIG replica count when enable_worker_ha=true. 2 survives a single VM/host failure; cross-zone resilience requires available GPU/CPU quota in a second zone."

  validation {
    condition     = var.worker_ha_target_size >= 1 && var.worker_ha_target_size <= 8
    error_message = "worker_ha_target_size must be between 1 and 8."
  }
}

variable "enable_cloud_armor" {
  type        = bool
  default     = false
  description = "When true, front Cloud Run with an External HTTPS LB + Cloud Armor (rate limiting, layer-7 DDoS defense) and restrict Cloud Run ingress to load-balancer-only (see cloud_armor.tf). Requires cloud_armor_domain and DNS pointed at the LB's static IP; the managed certificate needs DNS in place before it will provision. Default false preserves the documented pilot posture (public *.run.app URL, JWT-on-socket as the sole auth boundary)."
}

variable "enable_worker_saturation_log_metric" {
  type        = bool
  default     = false
  description = "Create a Cloud Monitoring alert on elevated worker_saturated soft-shed rates (requires a log-based metric named akshrava_worker_saturated)."
}

variable "cloud_armor_domain" {
  type        = string
  default     = ""
  description = "Domain for the External HTTPS LB's managed SSL certificate. Required when enable_cloud_armor=true; point its DNS A record at the akshrava_api_lb_ip output before applying, or the managed certificate will stay PROVISIONING indefinitely."
}
