# Optional Cloud Armor + External HTTPS Load Balancer in front of Cloud Run.
#
# Default (enable_cloud_armor=false) leaves the documented pilot posture untouched: Cloud Run's
# default *.run.app URL, `ingress = INGRESS_TRAFFIC_ALL`, and the JWT-on-the-WebSocket auth
# boundary (OPERATIONS.md "Current pilot configuration"). That is a deliberate, signed-off
# supervised-pilot trade-off, not an oversight -- do not flip this on without reviewing a
# `terraform plan` and provisioning the DNS + managed-certificate lead time it requires.
#
# The gap this closes when enabled: an unauthenticated WSS handshake reaches the Cloud Run
# container and consumes an instance slot (up to max_instance_count) before JWT verification
# ever runs in application code, since JWT is enforced by request-handling code, not by Cloud
# Run's own IAM/ingress layer when api_allow_unauthenticated=true. Cloud Armor rate-based rules
# reject a flood at the load balancer, before it ever reaches a container instance, and
# `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` blocks direct hits to the *.run.app URL entirely so
# the LB is the only path in.

locals {
  cloud_armor_enabled = var.enable_cloud_armor
}

resource "google_compute_security_policy" "api" {
  count       = local.cloud_armor_enabled ? 1 : 0
  name        = "akshrava-api-armor"
  description = "Rate-based protection for the public Akshrava phone WSS endpoint"

  # Default allow; the rate-based rule below converts a sustained per-IP flood into a temporary
  # ban rather than blocking any single request outright, since one phone reconnecting under
  # backoff+jitter (ProtocolClient) must not itself be misclassified as an attacker.
  rule {
    action   = "allow"
    priority = "2147483647"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow"
  }

  rule {
    action   = "throttle"
    priority = "1000"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 120
        interval_sec = 60
      }
    }
    description = "Per-IP throttle: well above one phone's normal reconnect rate, well below a flood"
  }

  rule {
    action   = "throttle"
    priority = "900"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
      ban_duration_sec = 300
      rate_limit_threshold {
        count        = 300
        interval_sec = 60
      }
      ban_threshold {
        count        = 300
        interval_sec = 60
      }
    }
    description = "Sustained flood from one IP: 5 minute ban, not just a 429"
  }

  adaptive_protection_config {
    layer_7_ddos_defense_config {
      enable = true
    }
  }
}

resource "google_compute_region_network_endpoint_group" "api" {
  count                 = local.cloud_armor_enabled ? 1 : 0
  name                  = "akshrava-api-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.api.name
  }
}

resource "google_compute_backend_service" "api" {
  count                 = local.cloud_armor_enabled ? 1 : 0
  name                  = "akshrava-api-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.api[0].id
  # WSS is a long-lived connection; do not let the LB's own idle timeout cut a quiet-but-healthy
  # session (session_admission's own lease/renew logic, not this timeout, is the intended
  # liveness signal -- see main.py _renew_or_readmit).
  timeout_sec = 3600

  backend {
    group = google_compute_region_network_endpoint_group.api[0].id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_url_map" "api" {
  count           = local.cloud_armor_enabled ? 1 : 0
  name            = "akshrava-api-lb"
  default_service = google_compute_backend_service.api[0].id
}

resource "google_compute_managed_ssl_certificate" "api" {
  count = local.cloud_armor_enabled ? 1 : 0
  name  = "akshrava-api-cert"
  managed {
    domains = [var.cloud_armor_domain]
  }
}

resource "google_compute_target_https_proxy" "api" {
  count            = local.cloud_armor_enabled ? 1 : 0
  name             = "akshrava-api-https-proxy"
  url_map          = google_compute_url_map.api[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.api[0].id]
}

resource "google_compute_global_address" "api_lb" {
  count = local.cloud_armor_enabled ? 1 : 0
  name  = "akshrava-api-lb-ip"
}

resource "google_compute_global_forwarding_rule" "api_https" {
  count                 = local.cloud_armor_enabled ? 1 : 0
  name                  = "akshrava-api-https-fwd"
  target                = google_compute_target_https_proxy.api[0].id
  port_range            = "443"
  ip_address            = google_compute_global_address.api_lb[0].id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}
