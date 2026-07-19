resource "google_sql_database_instance" "postgres" {
  name             = "akshrava-db-instance"
  database_version = "POSTGRES_15"
  region           = var.region
  depends_on       = [google_service_networking_connection.private_connection]

  settings {
    tier = "db-f1-micro"
    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
  }
  deletion_protection = false
}

resource "google_sql_database" "database" {
  name     = "akshrava"
  instance = google_sql_database_instance.postgres.name
}

resource "google_redis_instance" "redis" {
  name               = "akshrava-redis"
  tier               = "BASIC"
  memory_size_gb     = 1
  authorized_network = google_compute_network.vpc.id
  redis_version      = "REDIS_7_0"
  region             = var.region
}
