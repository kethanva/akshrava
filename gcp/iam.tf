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
  api_secret_ids = [
    google_secret_manager_secret.jwt_public.secret_id,
    google_secret_manager_secret.worker_shared.secret_id,
    google_secret_manager_secret.database_url.secret_id,
    google_secret_manager_secret.redis_url.secret_id,
    google_secret_manager_secret.worker_tls_ca.secret_id,
    google_secret_manager_secret.worker_tls_client_cert.secret_id,
    google_secret_manager_secret.worker_tls_client_key.secret_id,
  ]
  worker_secret_ids = [
    google_secret_manager_secret.worker_shared.secret_id,
    google_secret_manager_secret.nonce_redis_url.secret_id,
    google_secret_manager_secret.worker_tls_ca.secret_id,
    google_secret_manager_secret.worker_tls_server_cert.secret_id,
    google_secret_manager_secret.worker_tls_server_key.secret_id,
  ]
}

resource "google_secret_manager_secret_iam_member" "api_secret_accessor" {
  for_each  = toset(local.api_secret_ids)
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_secret_accessor" {
  for_each  = toset(local.worker_secret_ids)
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_sa.email}"
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
