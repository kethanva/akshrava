resource "google_compute_instance" "worker" {
  # Mutually exclusive with the worker_ha.tf MIG path: enabling enable_worker_ha replaces this
  # single VM rather than running alongside it.
  count        = (local.deploy_remote_worker && !local.worker_ha_enabled) ? 1 : 0
  name         = "akshrava-gpu-worker"
  machine_type = var.worker_machine_type != "" ? var.worker_machine_type : (var.worker_use_gpu ? "g2-standard-4" : "n2-standard-8")
  zone         = var.zone
  tags         = ["akshrava-worker"]

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
      size  = 100
      type  = "pd-balanced"
    }
  }

  dynamic "guest_accelerator" {
    for_each = var.worker_use_gpu ? [1] : []
    content {
      type  = "nvidia-l4"
      count = 1
    }
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
      project_id                   = var.project_id
      region                       = var.region
      worker_image                 = local.worker_image
      environment                  = var.environment
      yolo_weights_sha256          = var.yolo_weights_sha256
      worker_use_gpu               = var.worker_use_gpu
      worker_infer_timeout_seconds = local.worker_infer_timeout_seconds
    })
  }

  scheduling {
    on_host_maintenance = var.worker_use_gpu ? "TERMINATE" : "MIGRATE"
    automatic_restart   = true
  }

  allow_stopping_for_update = true

  depends_on = [
    google_secret_manager_secret_version.worker_shared,
    google_secret_manager_secret_version.nonce_redis_url,
    google_secret_manager_secret_version.worker_tls_ca,
    google_secret_manager_secret_version.worker_tls_server_cert,
    google_secret_manager_secret_version.worker_tls_server_key,
    google_secret_manager_secret_version.metrics_scrape_token,
    google_secret_manager_secret_version.redis_ca,
    google_artifact_registry_repository.containers,
  ]
}

resource "google_compute_firewall" "allow_run_to_worker_mtls" {
  count   = local.deploy_remote_worker ? 1 : 0
  name    = "akshrava-allow-run-to-worker-mtls"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["8443"]
  }

  source_ranges = [google_vpc_access_connector.connector.ip_cidr_range]
  target_tags   = ["akshrava-worker"]
}

# IAP TCP forwarding (35.235.240.0/20) for worker SSH diagnostics. Worker has no public IP.
resource "google_compute_firewall" "allow_iap_ssh" {
  name        = "allow-iap-ssh"
  network     = google_compute_network.vpc.name
  description = "Allow Identity-Aware Proxy SSH to akshrava worker VMs"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["akshrava-worker"]
}

resource "google_cloud_run_v2_job" "migrate" {
  name     = "akshrava-migrate"
  location = var.region

  template {
    template {
      service_account = google_service_account.api_sa.email
      timeout         = "600s"
      vpc_access {
        connector = google_vpc_access_connector.connector.id
        egress    = "PRIVATE_RANGES_ONLY"
      }
      containers {
        image   = local.api_image
        command = ["alembic", "upgrade", "head"]
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url_sync.secret_id
              version = "latest"
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_sql_user.db_user,
    google_secret_manager_secret_iam_member.api_secret_accessor,
  ]
}

resource "google_cloud_run_v2_service" "api" {
  name     = "akshrava-api"
  location = var.region
  # When Cloud Armor fronts the service (cloud_armor.tf), block direct hits to the *.run.app URL
  # entirely so the load balancer + security policy is the only path a phone (or an attacker) can
  # reach the container through.
  ingress = local.cloud_armor_enabled ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"

  template {
    # Long-lived WebSockets consume one concurrency slot each. Keep the per-instance buffer and
    # event-loop fan-in bounded; max_active_sessions=200 then scales across at most ten instances.
    max_instance_request_concurrency = 20
    service_account                  = google_service_account.api_sa.email
    timeout                          = "3600s"
    scaling {
      # Keep at least one warm instance for WSS reliability in pilot/production.
      min_instance_count = 1
      max_instance_count = 10
    }
    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    volumes {
      name = "jwt"
      secret {
        secret = google_secret_manager_secret.jwt_public.secret_id
        items {
          path    = "public.pem"
          version = "latest"
        }
      }
    }
    volumes {
      name = "jwt-previous"
      secret {
        secret = google_secret_manager_secret.jwt_public_previous.secret_id
        items {
          path    = "public-previous.pem"
          version = "latest"
        }
      }
    }
    volumes {
      name = "worker-mtls"
      secret {
        secret = google_secret_manager_secret.worker_tls_ca.secret_id
        items {
          path    = "ca.pem"
          version = "latest"
        }
      }
    }
    volumes {
      name = "worker-mtls-client-cert"
      secret {
        secret = google_secret_manager_secret.worker_tls_client_cert.secret_id
        items {
          path    = "client.pem"
          version = "latest"
        }
      }
    }
    volumes {
      name = "worker-mtls-client-key"
      secret {
        secret = google_secret_manager_secret.worker_tls_client_key.secret_id
        items {
          path    = "client.key"
          version = "latest"
        }
      }
    }
    dynamic "volumes" {
      for_each = var.redis_transit_encryption ? [1] : []
      content {
        name = "redis-ca"
        secret {
          secret = google_secret_manager_secret.redis_ca[0].secret_id
          items {
            path    = "ca.pem"
            version = "latest"
          }
        }
      }
    }

    containers {
      image = local.api_image
      ports {
        container_port = 8000
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        # Keep CPU allocated outside requests so WebSocket sessions stay alive.
        cpu_idle = false
      }

      startup_probe {
        http_get {
          path = "/readyz"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        timeout_seconds       = 3
        failure_threshold     = 12
      }
      liveness_probe {
        http_get {
          path = "/livez"
          port = 8000
        }
        period_seconds    = 30
        timeout_seconds   = 3
        failure_threshold = 3
      }

      volume_mounts {
        name       = "jwt"
        mount_path = "/run/secrets/jwt"
      }
      volume_mounts {
        name       = "jwt-previous"
        mount_path = "/run/secrets/jwt-previous"
      }
      volume_mounts {
        name       = "worker-mtls"
        mount_path = "/run/secrets/worker-mtls-ca"
      }
      volume_mounts {
        name       = "worker-mtls-client-cert"
        mount_path = "/run/secrets/worker-mtls-cert"
      }
      volume_mounts {
        name       = "worker-mtls-client-key"
        mount_path = "/run/secrets/worker-mtls-key"
      }
      dynamic "volume_mounts" {
        for_each = var.redis_transit_encryption ? [1] : []
        content {
          name       = "redis-ca"
          mount_path = "/run/secrets/redis-ca"
        }
      }

      env {
        name  = "AKSHRAVA_ENV"
        value = var.environment
      }
      dynamic "env" {
        for_each = var.otlp_exporter_endpoint != "" ? [var.otlp_exporter_endpoint] : []
        content {
          name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
          value = env.value
        }
      }
      env {
        name  = "DETECTOR"
        value = var.detector
      }
      env {
        name  = "JWT_ALGORITHM"
        value = "RS256"
      }
      env {
        name  = "JWT_PUBLIC_KEY_FILE"
        value = "/run/secrets/jwt/public.pem"
      }
      env {
        # Dual-key cutover path used by rotate_jwt_rs256.sh. Seeded equal to current at first
        # apply; auth.py skips verify when previous PEM equals current.
        name  = "JWT_PUBLIC_KEY_PREVIOUS_FILE"
        value = "/run/secrets/jwt-previous/public-previous.pem"
      }
      # JWT_SECRET is intentionally NOT set here: JWT_ALGORITHM is hardcoded RS256 above, and
      # config.py only reads/validates jwt_secret when the algorithm is HS256. A static
      # placeholder string in production IaC is confusing during security audits and, more
      # importantly, is a live symmetric secret sitting in state/logs for a value the app never
      # needs -- remove it rather than keep a "disabled" fallback around to explain.
      env {
        name  = "REMOTE_INFERENCE_URL"
        value = local.deploy_remote_worker ? local.remote_inference_url : ""
      }
      env {
        name  = "REMOTE_TLS_CA_FILE"
        value = "/run/secrets/worker-mtls-ca/ca.pem"
      }
      env {
        name  = "REMOTE_TLS_CLIENT_CERT_FILE"
        value = "/run/secrets/worker-mtls-cert/client.pem"
      }
      env {
        name  = "REMOTE_TLS_CLIENT_KEY_FILE"
        value = "/run/secrets/worker-mtls-key/client.key"
      }
      dynamic "env" {
        for_each = var.redis_transit_encryption ? [1] : []
        content {
          name  = "REDIS_CA_CERT_FILE"
          value = "/run/secrets/redis-ca/ca.pem"
        }
      }
      env {
        name  = "GCP_DIAGNOSTICS_BUCKET"
        value = google_storage_bucket.diagnostics.name
      }
      env {
        name  = "DIAGNOSTIC_UPLOADS_ENABLED"
        value = "false"
      }
      env {
        name  = "DATABASE_SCHEMA_REVISION"
        value = var.database_schema_revision
      }
      env {
        name  = "DEV_AUTH_BYPASS"
        value = "false"
      }
      env {
        name = "ALERT_MAX_AGE_MS"
        # Shared server/phone freshness ceiling. A slow CPU bench result can still be counted as
        # late telemetry, but it must never score a hazard at 8.5 s and invite a phone to speak
        # about a frame the user captured many seconds earlier.
        value = "2500"
      }
      env {
        name  = "MIN_FRAME_INTERVAL_MS"
        value = "200"
      }
      env {
        name  = "ALERT_RETENTION_DAYS"
        value = "30"
      }
      env {
        name  = "MAX_ACTIVE_SESSIONS"
        value = "200"
      }
      env {
        name = "INFERENCE_TIMEOUT_MS"
        # CPU remote remains a bench option, but it gets no larger user-facing freshness budget.
        # Sustained inability to answer inside 2.5 s opens the circuit and is announced by phones
        # instead of leaving a nominally connected session silent indefinitely.
        value = var.worker_use_gpu || var.detector != "remote" ? "800" : "2500"
      }
      env {
        name  = "REMOTE_INFERENCE_TIMEOUT_MS"
        value = tostring(local.remote_inference_timeout_ms)
      }
      env {
        name  = "INFERENCE_EXECUTOR_WORKERS"
        value = "2"
      }
      env {
        name  = "CLOUD_FALLBACK_PROVIDER"
        value = "none"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "REDIS_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.redis_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "REMOTE_WORKER_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.worker_shared.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "METRICS_SCRAPE_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.metrics_scrape_token.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.api_secret_accessor,
    google_vpc_access_connector.connector,
    google_redis_instance.cache,
    google_sql_user.db_user,
    google_cloud_run_v2_job.migrate,
  ]
}

# Prefer authenticated invokers. Public allUsers is opt-in via api_allow_unauthenticated.
resource "google_cloud_run_v2_service_iam_member" "api_public_invoker" {
  count    = var.api_allow_unauthenticated ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "api_invokers" {
  for_each = toset(var.api_invoker_members)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = each.value
}
