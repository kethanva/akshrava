resource "google_compute_network" "vpc" {
  name                    = "akshrava-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "subnet_app" {
  name                     = "akshrava-subnet-app"
  ip_cidr_range            = "10.0.1.0/24"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  private_ip_google_access = true
}

resource "google_compute_subnetwork" "subnet_workers" {
  name                     = "akshrava-subnet-workers"
  ip_cidr_range            = "10.0.2.0/24"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  private_ip_google_access = true
}

resource "google_compute_global_address" "private_ip_alloc" {
  name          = "akshrava-private-ip-alloc"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]
}

resource "google_compute_router" "router" {
  name    = "akshrava-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "akshrava-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_vpc_access_connector" "connector" {
  name          = "akshrava-connector"
  ip_cidr_range = "10.8.0.0/28"
  network       = google_compute_network.vpc.name
  region        = var.region
  depends_on    = [google_project_service.required]
}

resource "google_dns_managed_zone" "internal" {
  name        = "akshrava-internal"
  dns_name    = "akshrava.internal."
  description = "Private DNS for control-plane to GPU worker mTLS"
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc.id
    }
  }
}

resource "google_dns_record_set" "worker" {
  count        = local.deploy_remote_worker ? 1 : 0
  name         = "worker.akshrava.internal."
  type         = "A"
  ttl          = 30
  managed_zone = google_dns_managed_zone.internal.name
  # HA mode (worker_ha.tf) replaces the single VM with a regional MIG behind an internal LB;
  # point the same internal DNS name at whichever backend is actually deployed.
  rrdatas = [
    local.worker_ha_enabled
    ? google_compute_forwarding_rule.worker_ilb[0].ip_address
    : google_compute_instance.worker[0].network_interface[0].network_ip
  ]
}
