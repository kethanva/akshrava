output "cloud_run_url" {
  description = "Public HTTPS base URL for the phone WSS endpoint (append /v1/session)."
  value       = google_cloud_run_v2_service.api.uri
}

output "websocket_url" {
  description = "Phone ProtocolClient endpoint."
  value       = "${replace(google_cloud_run_v2_service.api.uri, "https://", "wss://")}/v1/session"
}

output "artifact_registry" {
  description = "Docker repository for API and worker images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/akshrava"
}

output "diagnostics_bucket" {
  description = "GCS bucket for consented diagnostic frames."
  value       = google_storage_bucket.diagnostics.name
}

output "worker_internal_url" {
  description = "Private mTLS inference URL used by the API."
  value       = local.remote_inference_url
}

output "migrate_job" {
  description = "Run after first apply (and after schema changes): gcloud run jobs execute akshrava-migrate --region <region> --wait"
  value       = google_cloud_run_v2_job.migrate.name
}

output "jwt_private_key_secret" {
  description = "Secret Manager id for the provisioning-workstation private key. Never mount on the API."
  value       = google_secret_manager_secret.jwt_private.secret_id
}

output "build_and_push_hint" {
  description = "Build images before the first successful Cloud Run / worker boot."
  value       = " ./scripts/build_gcp_images.sh ${var.project_id} ${var.region}"
}
