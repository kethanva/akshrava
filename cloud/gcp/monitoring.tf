# Cloud Monitoring SLI/SLO scaffolding for the Akshrava pilot.
#
# These policies are intentionally conservative: they alert on control-plane reachability
# and elevated soft-shed rates without exporting device identifiers.

resource "google_monitoring_uptime_check_config" "api_readyz" {
  display_name = "akshrava-api-readyz"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/readyz"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimprefix(google_cloud_run_v2_service.api.uri, "https://")
    }
  }
}

resource "google_monitoring_alert_policy" "api_uptime" {
  display_name = "akshrava-api-uptime-failed"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.labels.check_id=\"${google_monitoring_uptime_check_config.api_readyz.uptime_check_id}\""
      duration        = "300s"
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_NEXT_OLDER"
      }
    }
  }

  documentation {
    content   = "Akshrava API /readyz uptime check failed for 5 minutes. Check Cloud Run revisions and worker health."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_alert_policy" "worker_saturated_slo" {
  count        = var.enable_worker_saturation_log_metric ? 1 : 0
  display_name = "akshrava-worker-saturated-elevated"
  combiner     = "OR"

  conditions {
    display_name = "worker_saturated log rate elevated"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/akshrava_worker_saturated\" AND resource.type=\"cloud_run_revision\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 20
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  documentation {
    content   = "Worker saturation soft-sheds are elevated. Scale the worker MIG (enable_worker_ha) or reduce client FPS."
    mime_type = "text/markdown"
  }
}

# DB pool / custom-service SLO alerts require metrics that only appear after the new API revision
# scrapes Prometheus and a GCLB/Cloud Run monitored service type. Keep them out of the default
# apply path so pilot deploys are not blocked by missing metric descriptors.

