# Service Accounts
resource "google_service_account" "api_sa" {
  account_id   = "akshrava-api-sa"
  display_name = "Akshrava API Service Account"
}

resource "google_service_account" "worker_sa" {
  account_id   = "akshrava-worker-sa"
  display_name = "Akshrava Worker Service Account"
}

# Cloud Run API Gateway
resource "google_cloud_run_v2_service" "api" {
  name     = "akshrava-api"
  location = var.region

  template {
    service_account = google_service_account.api_sa.email
    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
    containers {
      image = "gcr.io/${var.project_id}/akshrava-api:latest"
      ports {
        container_port = 8000
      }
      env {
        name  = "AKSHRAVA_ENV"
        value = "pilot"
      }
      env {
        name  = "DATABASE_URL"
        value = "postgresql+asyncpg://akshrava:${google_sql_database_instance.postgres.private_ip_address}/akshrava"
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.redis.host}:6379/0"
      }
      env {
        name = "JWT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.jwt_secret.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# GCE GPU Worker VM Template
resource "google_compute_instance_template" "worker_template" {
  name_prefix  = "akshrava-gpu-worker-"
  machine_type = "g2-standard-4"
  region       = var.region

  disk {
    source_image = "cos-cloud/cos-stable"
    auto_delete  = true
    boot         = true
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
    user-data = <<EOF
#cloud-config
runcmd:
  - cos-extensions install gpu
  - docker run --device /dev/nvidia0 --device /dev/nvidia-uvm --device /dev/nvidiactl -d -p 8000:8000 gcr.io/${var.project_id}/akshrava-worker:latest
EOF
  }

  lifecycle {
    create_before_destroy = true
  }
}

# GPU Managed Instance Group (MIG) with Auto-Scaling
resource "google_compute_region_instance_group_manager" "worker_mig" {
  name               = "akshrava-worker-mig"
  region             = var.region
  base_instance_name = "akshrava-worker"
  target_size        = 1

  version {
    instance_template = google_compute_instance_template.worker_template.id
  }

  named_port {
    name = "http"
    port = 8000
  }
}

resource "google_compute_region_autoscaler" "worker_autoscaler" {
  name   = "akshrava-worker-autoscaler"
  region = var.region
  target = google_compute_region_instance_group_manager.worker_mig.id

  autoscaling_policy {
    max_replicas    = 5
    min_replicas    = 1
    cooldown_period = 60

    cpu_utilization {
      target = 0.8
    }
  }
}

# Google Cloud HTTPS Load Balancer with Cloud Run backend
resource "google_compute_region_network_endpoint_group" "serverless_neg" {
  name                  = "akshrava-serverless-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region
  cloud_run {
    service = google_cloud_run_v2_service.api.name
  }
}

resource "google_compute_backend_service" "lb_backend" {
  name                  = "akshrava-lb-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.serverless_neg.id
  }
}

resource "google_compute_url_map" "url_map" {
  name            = "akshrava-url-map"
  default_service = google_compute_backend_service.lb_backend.id
}

resource "google_compute_managed_ssl_certificate" "cert" {
  name = "akshrava-ssl-cert"
  managed {
    domains = [var.domain]
  }
}

resource "google_compute_target_https_proxy" "https_proxy" {
  name             = "akshrava-https-proxy"
  url_map          = google_compute_url_map.url_map.id
  ssl_certificates = [google_compute_managed_ssl_certificate.cert.id]
}

resource "google_compute_global_forwarding_rule" "forwarding_rule" {
  name                  = "akshrava-lb-forwarding-rule"
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "443"
  target                = google_compute_target_https_proxy.https_proxy.id
}
