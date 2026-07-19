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
  ingress  = "INGRESS_TRAFFIC_ALL" # Allow public internet clients directly (with free auto-managed SSL)

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
      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
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
        name  = "DETECTOR"
        value = "remote"
      }
      env {
        name  = "REMOTE_INFERENCE_URL"
        value = "http://${google_compute_instance.worker.network_interface[0].network_ip}:8000/v1/infer"
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
      env {
        name = "REMOTE_WORKER_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.worker_secret.secret_id
            version = "latest"
          }
        }
      }
    }
  }
}

# Standalone Compute Engine VM for GPU Inference (NVIDIA L4)
resource "google_compute_instance" "worker" {
  name         = "akshrava-gpu-worker"
  machine_type = "g2-standard-4" # 4 vCPUs, 16GB RAM, 1x NVIDIA L4 GPU (cost-efficient, high throughput for 100 FPS)
  zone         = var.zone
  tags         = ["akshrava-worker"]

  boot_disk {
    initialize_params {
      image = "cos-cloud/cos-stable"
      size  = 50 # 50GB boot disk for OS & Docker images
    }
  }

  guest_accelerator {
    type  = "nvidia-l4"
    count = 1
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet_workers.id
    # No public IP assigned to the VM; fully private. Incoming traffic only from VPC/Cloud Run.
  }

  service_account {
    email  = google_service_account.worker_sa.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    # Startup script automatically installs GPU drivers and spins up the Worker docker container
    user-data = <<EOF
#cloud-config
runcmd:
  - cos-extensions install gpu
  - docker run --device /dev/nvidia0 --device /dev/nvidia-uvm --device /dev/nvidiactl -d -p 8000:8000 gcr.io/${var.project_id}/akshrava-worker:latest
EOF
  }

  # Ensure the VM is schedulable on GPU hosts
  scheduling {
    on_host_maintenance = "TERMINATE" # Required for GPUs on GCP
    automatic_restart   = true
  }
}

# Allow HTTP traffic from the Serverless VPC Connector to the Worker VM
resource "google_compute_firewall" "allow_run_to_worker" {
  name    = "akshrava-allow-run-to-worker"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }

  source_ranges = [google_vpc_access_connector.connector.ip_cidr_range]
  target_tags   = ["akshrava-worker"]
}
