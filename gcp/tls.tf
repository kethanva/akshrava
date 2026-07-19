# Device JWT: API mounts only the public key. Private key is an output for the provisioning workstation.
resource "tls_private_key" "jwt" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# Private CA for control-plane ↔ GPU worker mTLS.
resource "tls_private_key" "worker_ca" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "worker_ca" {
  private_key_pem = tls_private_key.worker_ca.private_key_pem

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
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "worker_server" {
  private_key_pem = tls_private_key.worker_server.private_key_pem

  subject {
    common_name = "worker.akshrava.internal"
  }

  dns_names = ["worker.akshrava.internal"]
}

resource "tls_locally_signed_cert" "worker_server" {
  cert_request_pem   = tls_cert_request.worker_server.cert_request_pem
  ca_private_key_pem = tls_private_key.worker_ca.private_key_pem
  ca_cert_pem        = tls_self_signed_cert.worker_ca.cert_pem

  validity_period_hours = 24 * 365 * 2

  allowed_uses = [
    "digital_signature",
    "key_encipherment",
    "server_auth",
  ]
}

resource "tls_private_key" "worker_client" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_cert_request" "worker_client" {
  private_key_pem = tls_private_key.worker_client.private_key_pem

  subject {
    common_name = "akshrava-api"
  }
}

resource "tls_locally_signed_cert" "worker_client" {
  cert_request_pem   = tls_cert_request.worker_client.cert_request_pem
  ca_private_key_pem = tls_private_key.worker_ca.private_key_pem
  ca_cert_pem        = tls_self_signed_cert.worker_ca.cert_pem

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
