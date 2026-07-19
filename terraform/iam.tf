# IAM Bindings for API Service Account

# Secret Manager Access
resource "google_secret_manager_secret_iam_member" "api_jwt_secret_accessor" {
  secret_id = google_secret_manager_secret.jwt_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "api_worker_secret_accessor" {
  secret_id = google_secret_manager_secret.worker_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_sa.email}"
}

# Cloud Storage Access (Diagnostic Frames)
resource "google_storage_bucket_iam_member" "api_storage_creator" {
  bucket = google_storage_bucket.diagnostics.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.api_sa.email}"
}

# Artifact Registry Reader for both Service Accounts (to pull container images)
resource "google_project_iam_member" "api_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.api_sa.email}"
}

resource "google_project_iam_member" "worker_artifact_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.worker_sa.email}"
}
