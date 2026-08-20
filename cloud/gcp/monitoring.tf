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
      host       = local.cloud_armor_enabled ? var.cloud_armor_domain : trimprefix(google_cloud_run_v2_service.api.uri, "https://")
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

  notification_channels = var.monitoring_notification_channels

  documentation {
    content   = "Akshrava API /readyz uptime check failed for 5 minutes. Check Cloud Run revisions and worker health."
    mime_type = "text/markdown"
  }

  lifecycle {
    precondition {
      condition     = var.environment != "production" || length(var.monitoring_notification_channels) > 0
      error_message = "production requires at least one monitoring_notification_channels entry; see OPERATIONS.md."
    }
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

  notification_channels = var.monitoring_notification_channels

  documentation {
    content   = "Worker saturation soft-sheds are elevated. Scale the worker MIG (enable_worker_ha) or reduce client FPS."
    mime_type = "text/markdown"
  }
}

# The API emits one aggregate, identifier-free delivery window per minute rather than a log entry
# for every frame. These DELTA metrics establish whether successful server-side WebSocket writes
# are being acknowledged by phones and whether those phones still accept results within their own
# freshness gate. Cloud Run does not scrape the API's private /metrics endpoint automatically.
resource "google_logging_metric" "api_results_sent" {
  name            = "akshrava_results_sent"
  description     = "Aggregate results accepted by the API WebSocket transport. This is not handset delivery."
  filter          = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"phone_delivery_window\""
  value_extractor = "EXTRACT(jsonPayload.results_sent)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "api_phone_results_acknowledged" {
  name            = "akshrava_phone_results_acknowledged"
  description     = "Aggregate results explicitly processed by authenticated phones."
  filter          = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"phone_delivery_window\""
  value_extractor = "EXTRACT(jsonPayload.phone_results_acknowledged)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "api_result_acknowledgements_expected" {
  name            = "akshrava_result_acknowledgements_expected"
  description     = "Aggregate sent results for phones that advertise result acknowledgement support."
  filter          = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"phone_delivery_window\""
  value_extractor = "EXTRACT(jsonPayload.result_acknowledgements_expected)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "api_phone_results_acknowledged_fresh" {
  name            = "akshrava_phone_results_acknowledged_fresh"
  description     = "Aggregate phone-acknowledged results inside the phone freshness gate."
  filter          = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"phone_delivery_window\""
  value_extractor = "EXTRACT(jsonPayload.phone_results_acknowledged_fresh)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_logging_metric" "api_phone_results_acknowledged_missing" {
  name            = "akshrava_phone_results_acknowledged_missing"
  description     = "Sent results whose bounded acknowledgement slot was evicted with no acknowledgement ever received. Exact count, not a window subtraction."
  filter          = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"phone_delivery_window\""
  value_extractor = "EXTRACT(jsonPayload.phone_results_acknowledged_missing)"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "phone_result_ack_missing" {
  display_name = "akshrava-phone-result-acknowledgements-missing"
  combiner     = "OR"

  # This is an exact count of result slots evicted without acknowledgement (see
  # Metrics.phone_result_unacknowledged), so acknowledgements straddling export boundaries do not
  # create false positives. A plain threshold remains fully plan-validatable without MQL.
  conditions {
    display_name = "Phone acknowledgements missing"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.api_phone_results_acknowledged_missing.name}\" AND resource.type=\"cloud_run_revision\""
      duration        = "900s"
      comparison      = "COMPARISON_GT"
      threshold_value = 10
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = var.monitoring_notification_channels

  documentation {
    content   = "Results were sent to phones that advertised acknowledgement support and were never acknowledged, sustained over 15 minutes. Check API revisions, WebSocket transport, and handset connectivity. This signal reports receipt/freshness processing, not TTS playback."
    mime_type = "text/markdown"
  }
}

# DB pool / custom-service SLO alerts require metrics that only appear after the new API revision
# scrapes Prometheus and a GCLB/Cloud Run monitored service type. Keep them out of the default
# apply path so pilot deploys are not blocked by missing metric descriptors.
