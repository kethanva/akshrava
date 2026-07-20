resource "google_service_account" "api_sa" {
  account_id   = "akshrava-api-sa"
  display_name = "Akshrava API Service Account"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "worker_sa" {
  account_id   = "akshrava-worker-sa"
  display_name = "Akshrava Worker Service Account"
}

locals {
  # Static keys so for_each is known at plan time (secret_id strings are fixed in config).
  api_secret_ids = toset(concat([
    "akshrava-jwt-public",
    "akshrava-jwt-public-previous",
    "akshrava-worker-secret",
    "akshrava-database-url",
    "akshrava-database-url-sync",
    "akshrava-redis-url",
    "akshrava-worker-tls-ca",
    "akshrava-worker-tls-client-cert",
    "akshrava-worker-tls-client-key",
    "akshrava-metrics-scrape-token",
  ], var.redis_transit_encryption ? ["akshrava-redis-ca"] : []))
  worker_secret_ids = toset(concat([
    "akshrava-worker-secret",
    "akshrava-nonce-redis-url",
    "akshrava-worker-tls-ca",
    "akshrava-worker-tls-server-cert",
    "akshrava-worker-tls-server-key",
    "akshrava-metrics-scrape-token",
  ], var.redis_transit_encryption ? ["akshrava-redis-ca"] : []))
}

resource "google_secret_manager_secret_iam_member" "api_secret_accessor" {
  for_each  = local.api_secret_ids
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_sa.email}"
  # Secret Manager can 404 briefly after create; pin IAM after secrets exist.
  depends_on = [
    google_secret_manager_secret.redis_ca,
    google_secret_manager_secret.jwt_public_previous,
  ]
}

resource "google_secret_manager_secret_iam_member" "worker_secret_accessor" {
  for_each   = local.worker_secret_ids
  secret_id  = each.value
  role       = "roles/secretmanager.secretAccessor"
  member     = "serviceAccount:${google_service_account.worker_sa.email}"
  depends_on = [google_secret_manager_secret.redis_ca]
}

resource "google_storage_bucket_iam_member" "api_storage_creator" {
  bucket = google_storage_bucket.diagnostics.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_artifact_registry_repository_iam_member" "api_ar_reader" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.containers.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_artifact_registry_repository_iam_member" "worker_ar_reader" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.containers.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.worker_sa.email}"
}

# Cloud Run uses the default compute SA to pull unless the runtime SA is also granted.
resource "google_artifact_registry_repository_iam_member" "cloudrun_ar_reader" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.containers.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.api_sa.email}"
}

# COS metadata enables google-logging; the worker SA still needs IAM to write log entries.
resource "google_project_iam_member" "worker_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}

resource "google_project_iam_member" "api_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.api_sa.email}"
}
