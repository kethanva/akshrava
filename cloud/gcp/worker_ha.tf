# Optional highly-available GPU/CPU worker deployment.
#
# The default (enable_worker_ha=false) keeps the single-VM `google_compute_instance.worker` in
# app.tf untouched -- that is the deployed, documented pilot posture (OPERATIONS.md "Current
# pilot configuration"). This file adds an opt-in path to a regional Managed Instance Group
# behind an internal passthrough Network Load Balancer with auto-healing, so a zone outage or
# VM crash does not take down remote inference for every connected phone. Flipping this on is a
# real infrastructure change (new instances, new health check, new internal forwarding rule) --
# review a `terraform plan` against the live project before applying, same as any other
# HA/production-hardening variable in this stack.
#
# Traffic model: Cloud Run reaches the worker over mTLS on :8443 (Caddy sidecar terminates TLS
# and forwards to the FastAPI app on 127.0.0.1:8000). An internal L4 passthrough LB is used --
# not an HTTPS LB -- specifically because Caddy needs the raw mTLS handshake with the phone's
# client cert; terminating TLS at a Google-managed LB would break client-certificate auth.

locals {
  worker_ha_enabled = var.enable_worker_ha && local.deploy_remote_worker
}

resource "google_compute_instance_template" "worker" {
  count        = local.worker_ha_enabled ? 1 : 0
  name_prefix  = "akshrava-worker-tmpl-"
  machine_type = var.worker_machine_type != "" ? var.worker_machine_type : (var.worker_use_gpu ? "g2-standard-4" : "n2-standard-8")
  region       = var.region
  tags         = ["akshrava-worker"]

  disk {
    source_image = "cos-cloud/cos-stable"
    disk_size_gb = 100
    disk_type    = "pd-balanced"
    boot         = true
  }

  dynamic "guest_accelerator" {
    for_each = var.worker_use_gpu ? [1] : []
    content {
      type  = "nvidia-l4"
      count = 1
    }
  }

  scheduling {
    on_host_maintenance = var.worker_use_gpu ? "TERMINATE" : "MIGRATE"
    automatic_restart   = true
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet_workers.id
  }

  service_account {
    email  = google_service_account.worker_sa.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    google-logging-enabled = "true"
    startup-script = templatefile("${path.module}/scripts/worker-startup.sh.tftpl", {
      project_id          = var.project_id
      region              = var.region
      worker_image        = local.worker_image
      environment         = var.environment
      yolo_weights_sha256 = var.yolo_weights_sha256
      worker_use_gpu      = var.worker_use_gpu
    })
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    google_secret_manager_secret_version.worker_shared,
    google_secret_manager_secret_version.nonce_redis_url,
    google_secret_manager_secret_version.worker_tls_ca,
    google_secret_manager_secret_version.worker_tls_server_cert,
    google_secret_manager_secret_version.worker_tls_server_key,
    google_secret_manager_secret_version.metrics_scrape_token,
    google_artifact_registry_repository.containers,
  ]
}

# TCP-only: the health check cannot complete an mTLS handshake (it presents no client
# certificate), so it only verifies the port accepts a connection, not that a request succeeds.
# /healthz on the FastAPI app behind Caddy is verified operationally, not by this check.
resource "google_compute_region_health_check" "worker" {
  count               = local.worker_ha_enabled ? 1 : 0
  name                = "akshrava-worker-health"
  region              = var.region
  check_interval_sec  = 10
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 3

  tcp_health_check {
    port = 8443
  }
}

resource "google_compute_region_instance_group_manager" "worker" {
  count              = local.worker_ha_enabled ? 1 : 0
  name               = "akshrava-worker-mig"
  region             = var.region
  base_instance_name = "akshrava-worker"
  # Two replicas by default: an auto-healing single-VM MIG technically survives a VM crash but
  # not a zone-scoped GCE incident. var.worker_ha_target_size raises this once available quota
  # (NVIDIA_L4 or CPU) supports it across zones.
  target_size = var.worker_ha_target_size

  version {
    instance_template = google_compute_instance_template.worker[0].id
  }

  auto_healing_policies {
    health_check      = google_compute_region_health_check.worker[0].id
    initial_delay_sec = 180 # model load + weight SHA verification before it's judged unhealthy
  }

  update_policy {
    type                  = "PROACTIVE"
    minimal_action        = "REPLACE"
    max_surge_fixed       = 1
    max_unavailable_fixed = 0
  }
}

# GCP's own health-check probers, not VPC-internal clients -- required in addition to the
# existing allow_run_to_worker_mtls rule (which only covers the Serverless VPC Access CIDR).
resource "google_compute_firewall" "allow_health_check_to_worker" {
  count       = local.worker_ha_enabled ? 1 : 0
  name        = "akshrava-allow-health-check-to-worker"
  network     = google_compute_network.vpc.name
  description = "Allow Google Cloud health check probers to reach the worker MIG on :8443"

  allow {
    protocol = "tcp"
    ports    = ["8443"]
  }

  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
  target_tags   = ["akshrava-worker"]
}

resource "google_compute_region_backend_service" "worker" {
  count                 = local.worker_ha_enabled ? 1 : 0
  name                  = "akshrava-worker-ilb"
  region                = var.region
  protocol              = "TCP"
  load_balancing_scheme = "INTERNAL"
  health_checks         = [google_compute_region_health_check.worker[0].id]

  backend {
    group          = google_compute_region_instance_group_manager.worker[0].instance_group
    balancing_mode = "CONNECTION"
  }
}

resource "google_compute_forwarding_rule" "worker_ilb" {
  count                 = local.worker_ha_enabled ? 1 : 0
  name                  = "akshrava-worker-ilb-fwd"
  region                = var.region
  load_balancing_scheme = "INTERNAL"
  backend_service       = google_compute_region_backend_service.worker[0].id
  network               = google_compute_network.vpc.name
  subnetwork            = google_compute_subnetwork.subnet_workers.id
  ports                 = ["8443"]
  all_ports             = false
}
