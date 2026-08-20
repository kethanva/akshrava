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
  default     = "20260721_01"
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
  description = "When true, use SERVER_AUTHENTICATION transit encryption and rediss:// URLs. Requires redis_tier=STANDARD_HA (BASIC cannot terminate TLS)."
}

# Availability and encryption are separate decisions and must be separately expressible.
#
# These were previously one control: `tier = var.redis_transit_encryption ? "STANDARD_HA" : "BASIC"`.
# That made a *security* toggle silently decide an *availability* property, so
# `redis_transit_encryption = false` also meant "single-node Redis with no failover" -- and Redis
# is a hard dependency of session admission, frame rate limiting, and worker replay protection.
# Losing it takes assistance away from every connected phone at once. Nobody turning off TLS for a
# local bench intends to also remove failover from the fleet's most critical dependency.
variable "redis_tier" {
  type        = string
  default     = "STANDARD_HA"
  description = "Memorystore tier. STANDARD_HA provides a failover replica and is required for transit encryption; BASIC is single-node with no failover and is only appropriate for a throwaway development project."

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.redis_tier)
    error_message = "redis_tier must be BASIC or STANDARD_HA."
  }

  # The cross-variable invariant (TLS requires STANDARD_HA) is enforced by a lifecycle
  # precondition on google_redis_instance.cache in database.tf: variable validation blocks
  # cannot reference another variable on the pinned Terraform 1.5.x used by CI.
}

variable "database_availability_type" {
  type        = string
  default     = "REGIONAL"
  description = "Cloud SQL availability. REGIONAL keeps a synchronous standby in a second zone with automatic failover; ZONAL is single-zone and makes the database a fleet-wide SPOF (readiness fails, all sessions end). Only development should use ZONAL."

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.database_availability_type)
    error_message = "database_availability_type must be ZONAL or REGIONAL."
  }
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
  type = bool
  # Defaults ON: a single worker VM is a fleet-wide single point of failure for the whole vision
  # path -- one host event silences every connected phone at once. An operator who deliberately
  # wants the cheaper single-VM bench posture must opt OUT in tfvars, so the unsafe configuration
  # is the one that requires a decision. (This description previously claimed "default false"
  # while the default was already true; a stale docstring on an availability control is how an
  # operator ends up believing they have HA when they do not.)
  default     = true
  description = "When true, replace the single-VM GPU/CPU worker with a regional Managed Instance Group behind an internal L4 LB with auto-healing (see worker_ha.tf). Default true because a single worker VM is a fleet-wide SPOF; set false only for a deliberately single-VM bench/pilot posture, and review a terraform plan before changing it against a live project."
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

variable "otlp_exporter_endpoint" {
  type        = string
  default     = ""
  description = "Optional OTLP/HTTP trace collector endpoint for the API, for example an in-VPC collector URL ending in /v1/traces. Leave empty to retain traceparent-only correlation. Do not place credentials in this value; use collector workload identity or a separately managed OTEL header secret."
}

variable "cloud_armor_domain" {
  type        = string
  default     = ""
  description = "Domain for the External HTTPS LB's managed SSL certificate. Required when enable_cloud_armor=true; point its DNS A record at the akshrava_api_lb_ip output before applying, or the managed certificate will stay PROVISIONING indefinitely."
}

variable "monitoring_notification_channels" {
  type        = list(string)
  default     = []
  description = "Cloud Monitoring notification channel IDs (projects/<p>/notificationChannels/<id>). An alert policy with no channel fires into an empty room: nobody is paged. Required in production."
}

variable "worker_infer_timeout_seconds" {
  type        = number
  default     = 0
  description = "GPU worker inference deadline. 0 derives it from worker_use_gpu (0.5 s GPU / 2.2 s CPU). Must stay below the API's REMOTE_INFERENCE_TIMEOUT_MS so the API sees a signed 504 rather than its own client timeout."

  validation {
    condition     = var.worker_infer_timeout_seconds == 0 || (var.worker_infer_timeout_seconds >= 0.2 && var.worker_infer_timeout_seconds <= 3)
    error_message = "worker_infer_timeout_seconds must be 0 (derive) or between 0.2 and 3."
  }
}

check "worker_deadline_below_api_deadline" {
  assert {
    condition     = var.detector != "remote" || local.worker_infer_timeout_seconds * 1000 < local.remote_inference_timeout_ms
    error_message = "The worker deadline must be strictly below REMOTE_INFERENCE_TIMEOUT_MS, or the API times out its own client and the circuit breaker attributes the failure to the network instead of the model."
  }
}

