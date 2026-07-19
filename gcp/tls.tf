# Device JWT and worker mTLS material.
#
# Private keys created by the tls provider are stored in Terraform state. Prefer generating
# long-lived PKI outside Terraform (openssl / cert-manager / Cloud KMS) and supplying PEMs via
# the jwt_* / worker_*_pem variables below so private key bytes never enter state. When
# manage_pki_in_terraform=true (bootstrap only): keys are generated here and land in Terraform
# state — rotate if state is copied. Prefer manage_pki_in_terraform=false with external PEMs.
# immediately copied into Secret Manager — rotate them if state is ever copied or leaked.
#
# Rotation after a state copy: replace Secret Manager versions for jwt-private, worker TLS keys,
# and worker shared secret; re-mint all device JWTs; restart API + worker.

resource "tls_private_key" "jwt" {
  count     = var.manage_pki_in_terraform ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 2048
}

# Private CA for control-plane ↔ GPU worker mTLS.
resource "tls_private_key" "worker_ca" {
  count     = var.manage_pki_in_terraform ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "worker_ca" {
  count           = var.manage_pki_in_terraform ? 1 : 0
  private_key_pem = tls_private_key.worker_ca[0].private_key_pem

  subject {
    common_name  = "akshrava-worker-ca"
    organization = "Akshrava"
  }

  validity_period_hours = 24 * 365 * 5
  is_ca_certificate     = true

  allowed_uses = [
    "cert_signing",
    "crl_signing",
  ]
}

resource "tls_private_key" "worker_server" {
  count     = var.manage_pki_in_terraform ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "worker_server" {
  count           = var.manage_pki_in_terraform ? 1 : 0
  private_key_pem = tls_private_key.worker_server[0].private_key_pem

  subject {
    common_name = "worker.akshrava.internal"
  }

  dns_names = ["worker.akshrava.internal"]
}

resource "tls_locally_signed_cert" "worker_server" {
  count              = var.manage_pki_in_terraform ? 1 : 0
  cert_request_pem   = tls_cert_request.worker_server[0].cert_request_pem
  ca_private_key_pem = tls_private_key.worker_ca[0].private_key_pem
  ca_cert_pem        = tls_self_signed_cert.worker_ca[0].cert_pem

  validity_period_hours = 24 * 365 * 2

  allowed_uses = [
    "digital_signature",
    "key_encipherment",
    "server_auth",
  ]
}

resource "tls_private_key" "worker_client" {
  count     = var.manage_pki_in_terraform ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "worker_client" {
  count           = var.manage_pki_in_terraform ? 1 : 0
  private_key_pem = tls_private_key.worker_client[0].private_key_pem

  subject {
    common_name = "akshrava-api"
  }
}

resource "tls_locally_signed_cert" "worker_client" {
  count              = var.manage_pki_in_terraform ? 1 : 0
  cert_request_pem   = tls_cert_request.worker_client[0].cert_request_pem
  ca_private_key_pem = tls_private_key.worker_ca[0].private_key_pem
  ca_cert_pem        = tls_self_signed_cert.worker_ca[0].cert_pem

  validity_period_hours = 24 * 365 * 2

  allowed_uses = [
    "digital_signature",
    "key_encipherment",
    "client_auth",
  ]
}

resource "random_password" "worker_shared_secret" {
  length  = 48
  special = false
}

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "random_password" "metrics_scrape_token" {
  length  = 32
  special = false
}

locals {
  jwt_public_pem         = var.manage_pki_in_terraform ? tls_private_key.jwt[0].public_key_pem : var.jwt_public_key_pem
  jwt_private_pem        = var.manage_pki_in_terraform ? tls_private_key.jwt[0].private_key_pem : var.jwt_private_key_pem
  worker_ca_cert_pem     = var.manage_pki_in_terraform ? tls_self_signed_cert.worker_ca[0].cert_pem : var.worker_ca_cert_pem
  worker_server_cert_pem = var.manage_pki_in_terraform ? tls_locally_signed_cert.worker_server[0].cert_pem : var.worker_server_cert_pem
  worker_server_key_pem  = var.manage_pki_in_terraform ? tls_private_key.worker_server[0].private_key_pem : var.worker_server_key_pem
  worker_client_cert_pem = var.manage_pki_in_terraform ? tls_locally_signed_cert.worker_client[0].cert_pem : var.worker_client_cert_pem
  worker_client_key_pem  = var.manage_pki_in_terraform ? tls_private_key.worker_client[0].private_key_pem : var.worker_client_key_pem
}
