resource "google_compute_instance" "worker" {
  count        = local.deploy_remote_worker ? 1 : 0
  name         = "akshrava-gpu-worker"
  machine_type = "g2-standard-4"
  zone         = var.zone
  tags         = ["akshrava-worker"]

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
      size  = 100
      type  = "pd-balanced"
    }
  }

  guest_accelerator {
    type  = "nvidia-l4"
    count = 1
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
    })
  }

  scheduling {
    on_host_maintenance = "TERMINATE"
    automatic_restart   = true
  }

  allow_stopping_for_update = true

  depends_on = [
    google_secret_manager_secret_version.worker_shared,
    google_secret_manager_secret_version.nonce_redis_url,
    google_secret_manager_secret_version.worker_tls_ca,
    google_secret_manager_secret_version.worker_tls_server_cert,
    google_secret_manager_secret_version.worker_tls_server_key,
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
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api_sa.email
    timeout         = "3600s"
    scaling {
      min_instance_count = var.environment == "production" ? 1 : 0
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
      }

      volume_mounts {
        name       = "jwt"
        mount_path = "/run/secrets/jwt"
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

      env {
        name  = "AKSHRAVA_ENV"
        value = var.environment
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
        name  = "JWT_SECRET"
        value = "unused-with-rs256-placeholder-at-least-32-chars"
      }
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
      env {
        name  = "GCP_DIAGNOSTICS_BUCKET"
        value = google_storage_bucket.diagnostics.name
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
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.api_secret_accessor,
    google_vpc_access_connector.connector,
    google_redis_instance.cache,
    google_sql_user.db_user,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "api_public_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
