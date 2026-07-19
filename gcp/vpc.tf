resource "google_compute_network" "vpc" {
  name                    = "akshrava-vpc"
  auto_create_subnetworks = false
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

# Private Service Connection for databases
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

# NAT Gateway for worker nodes to access external APIs securely (without public IPs)
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

# Serverless VPC Access connector for Cloud Run
resource "google_vpc_access_connector" "connector" {
  name          = "akshrava-connector"
  ip_cidr_range = "10.8.0.0/28"
  network       = google_compute_network.vpc.name
  region        = var.region
}
