resource "google_sql_database_instance" "postgres" {
  name             = "akshrava-db-instance"
  database_version = "POSTGRES_15"
  region           = var.region
  depends_on       = [google_service_networking_connection.private_connection]

  settings {
    tier = "db-custom-1-3840"
    # Explicit rather than inherited. The provider default is ZONAL, so omitting this silently
    # bought a single-zone database: a zone event takes Postgres down, /readyz then fails on
    # store.ping() and removes every API instance from service, which ends assistance for every
    # connected phone. REGIONAL adds a synchronous standby in a second zone with automatic
    # failover. Development may opt down to ZONAL to avoid paying for a standby.
    availability_type = var.database_availability_type
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
    # Pin the connection ceiling so it does not silently drift with tier defaults, and so it is
    # visibly reconciled with the backend's bounded pool: API pool is 5 + 3 overflow per Cloud Run
    # instance (max 10 instances = 80; rolling deploy = up to 160), leaving ample headroom under 200.
    database_flags {
      name  = "max_connections"
      value = "200"
    }
  }

  deletion_protection = var.environment != "development"
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
  name = "akshrava-redis"
  # Availability comes from redis_tier, NOT from the transit-encryption toggle. A variable
  # validation in variables.tf still rejects the impossible combination (TLS on BASIC), so the
  # two settings stay consistent without one silently deciding the other.
  tier                    = var.redis_tier
  memory_size_gb          = 1
  region                  = var.region
  authorized_network      = google_compute_network.vpc.id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  auth_enabled            = true
  redis_version           = "REDIS_7_0"
  transit_encryption_mode = var.redis_transit_encryption ? "SERVER_AUTHENTICATION" : "DISABLED"
  depends_on              = [google_service_networking_connection.private_connection]

  lifecycle {
    # Memorystore BASIC cannot terminate TLS, so this combination fails at apply time with an
    # opaque API error. Fail at plan time with an actionable message instead. This lives here
    # rather than in a variable validation block because Terraform 1.5.x (the CI pin) does not
    # allow a validation condition to reference a second variable.
    precondition {
      condition     = !(var.redis_transit_encryption && var.redis_tier == "BASIC")
      error_message = "redis_transit_encryption=true requires redis_tier=\"STANDARD_HA\"; Memorystore BASIC cannot terminate TLS."
    }
  }
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
