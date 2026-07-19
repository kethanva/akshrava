resource "google_sql_database_instance" "postgres" {
  name             = "akshrava-db-instance"
  database_version = "POSTGRES_15"
  region           = var.region
  depends_on       = [google_service_networking_connection.private_connection]

  settings {
    tier = "db-custom-1-3840"
    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = google_compute_network.vpc.id
      enable_private_path_for_google_cloud_services = true
    }
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
  }

  deletion_protection = var.environment == "production"
}

resource "google_sql_database" "database" {
  name     = "akshrava"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "db_user" {
  name     = "akshrava"
  instance = google_sql_database_instance.postgres.name
  password = random_password.db_password.result
}

resource "google_redis_instance" "cache" {
  name               = "akshrava-redis"
  tier               = "BASIC"
  memory_size_gb     = 1
  region             = var.region
  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
  auth_enabled       = true
  redis_version      = "REDIS_7_0"
  depends_on         = [google_service_networking_connection.private_connection]
}

resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = "akshrava"
  description   = "Akshrava API and GPU worker images"
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}

resource "google_storage_bucket" "diagnostics" {
  name                        = "akshrava-diagnostics-${var.project_id}"
  location                    = var.region
  force_destroy               = var.environment != "production"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required]
}
