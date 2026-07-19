terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.10.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "google" {
  project     = var.project_id
  region      = var.region
  credentials = var.credentials_file != "" ? file(var.credentials_file) : null
}

resource "google_project_service" "required" {
  for_each = toset([
    "run.googleapis.com",
    "compute.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "vpcaccess.googleapis.com",
    "servicenetworking.googleapis.com",
    "artifactregistry.googleapis.com",
    "dns.googleapis.com",
    "redis.googleapis.com",
    "iam.googleapis.com",
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}
