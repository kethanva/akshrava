variable "project_id" {
  type        = string
  description = "The GCP Project ID where resources will be provisioned."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "The GCP region for regional resources."
}

variable "domain" {
  type        = string
  description = "The public domain name mapped to the Akshrava gateway API."
}
