resource "google_secret_manager_secret" "jwt_secret" {
  secret_id = "akshrava-jwt-secret"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "akshrava-db-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "worker_secret" {
  secret_id = "akshrava-worker-secret"
  replication {
    auto {}
  }
}
