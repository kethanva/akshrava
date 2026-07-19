locals {
  api_image_default    = "${var.region}-docker.pkg.dev/${var.project_id}/akshrava/akshrava-api:latest"
  worker_image_default = "${var.region}-docker.pkg.dev/${var.project_id}/akshrava/akshrava-worker:latest"
  api_image            = var.api_image != "" ? var.api_image : local.api_image_default
  worker_image         = var.worker_image != "" ? var.worker_image : local.worker_image_default
  remote_inference_url = "https://worker.akshrava.internal:8443/v1/infer"
  database_url         = "postgresql+asyncpg://akshrava:${random_password.db_password.result}@${google_sql_database_instance.postgres.private_ip_address}:5432/akshrava"
  database_url_sync    = "postgresql://akshrava:${random_password.db_password.result}@${google_sql_database_instance.postgres.private_ip_address}:5432/akshrava"
  redis_url            = "redis://:${google_redis_instance.cache.auth_string}@${google_redis_instance.cache.host}:${google_redis_instance.cache.port}/0"
  nonce_redis_url      = "redis://:${google_redis_instance.cache.auth_string}@${google_redis_instance.cache.host}:${google_redis_instance.cache.port}/1"
  deploy_remote_worker = var.detector == "remote"
}

resource "google_secret_manager_secret" "jwt_public" {
  secret_id = "akshrava-jwt-public"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "jwt_public" {
  secret      = google_secret_manager_secret.jwt_public.id
  secret_data = tls_private_key.jwt.public_key_pem
}

resource "google_secret_manager_secret" "jwt_private" {
  secret_id = "akshrava-jwt-private"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "jwt_private" {
  secret      = google_secret_manager_secret.jwt_private.id
  secret_data = tls_private_key.jwt.private_key_pem
}

resource "google_secret_manager_secret" "worker_shared" {
  secret_id = "akshrava-worker-secret"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "worker_shared" {
  secret      = google_secret_manager_secret.worker_shared.id
  secret_data = random_password.worker_shared_secret.result
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "akshrava-db-password"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "akshrava-database-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = local.database_url
}

resource "google_secret_manager_secret" "database_url_sync" {
  secret_id = "akshrava-database-url-sync"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url_sync" {
  secret      = google_secret_manager_secret.database_url_sync.id
  secret_data = local.database_url_sync
}

resource "google_secret_manager_secret" "redis_url" {
  secret_id = "akshrava-redis-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "redis_url" {
  secret      = google_secret_manager_secret.redis_url.id
  secret_data = local.redis_url
}

resource "google_secret_manager_secret" "nonce_redis_url" {
  secret_id = "akshrava-nonce-redis-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "nonce_redis_url" {
  secret      = google_secret_manager_secret.nonce_redis_url.id
  secret_data = local.nonce_redis_url
}

resource "google_secret_manager_secret" "worker_tls_ca" {
  secret_id = "akshrava-worker-tls-ca"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "worker_tls_ca" {
  secret      = google_secret_manager_secret.worker_tls_ca.id
  secret_data = tls_self_signed_cert.worker_ca.cert_pem
}

resource "google_secret_manager_secret" "worker_tls_server_cert" {
  secret_id = "akshrava-worker-tls-server-cert"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "worker_tls_server_cert" {
  secret      = google_secret_manager_secret.worker_tls_server_cert.id
  secret_data = tls_locally_signed_cert.worker_server.cert_pem
}

resource "google_secret_manager_secret" "worker_tls_server_key" {
  secret_id = "akshrava-worker-tls-server-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "worker_tls_server_key" {
  secret      = google_secret_manager_secret.worker_tls_server_key.id
  secret_data = tls_private_key.worker_server.private_key_pem
}

resource "google_secret_manager_secret" "worker_tls_client_cert" {
  secret_id = "akshrava-worker-tls-client-cert"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "worker_tls_client_cert" {
  secret      = google_secret_manager_secret.worker_tls_client_cert.id
  secret_data = tls_locally_signed_cert.worker_client.cert_pem
}

resource "google_secret_manager_secret" "worker_tls_client_key" {
  secret_id = "akshrava-worker-tls-client-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "worker_tls_client_key" {
  secret      = google_secret_manager_secret.worker_tls_client_key.id
  secret_data = tls_private_key.worker_client.private_key_pem
}
