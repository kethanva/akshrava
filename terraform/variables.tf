variable "project_id" {
  type        = string
  description = "The GCP Project ID where resources will be provisioned."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "The GCP region for regional resources."
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "The GCP zone for the GCE worker VM."
}
