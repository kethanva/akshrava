import os
import glob
import re

IAC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../cloud/gcp"))

def get_all_tf_content():
    """Reads all .tf files in the IAC_DIR and returns their concatenated content."""
    content = []
    for tf_file in glob.glob(os.path.join(IAC_DIR, "*.tf")):
        with open(tf_file, "r") as f:
            content.append(f.read())
    return "\n".join(content)

def test_iac_vpc_structure():
    tf_content = get_all_tf_content()
    assert 'resource "google_compute_network" "vpc"' in tf_content, "VPC network is missing"
    assert 'resource "google_compute_subnetwork" "subnet_app"' in tf_content, "App Subnet is missing"
    assert 'resource "google_compute_subnetwork" "subnet_workers"' in tf_content, "Workers Subnet is missing"
    assert 'resource "google_vpc_access_connector" "connector"' in tf_content, "VPC connector is missing"
    assert 'resource "google_service_networking_connection" "private_connection"' in tf_content, "Private Services peering is missing"

def test_iac_app_structure():
    tf_content = get_all_tf_content()
    
    # Test Cloud Run API
    assert 'resource "google_cloud_run_v2_service" "api"' in tf_content, "Cloud Run API service is missing"
    assert re.search(r'name\s*=\s*"DATABASE_URL"', tf_content), "DATABASE_URL is missing from Cloud Run"
    assert re.search(r'name\s*=\s*"GCP_DIAGNOSTICS_BUCKET"', tf_content), "GCP_DIAGNOSTICS_BUCKET is missing from Cloud Run"
    assert re.search(r'name\s*=\s*"JWT_ALGORITHM"', tf_content), "JWT_ALGORITHM is missing from Cloud Run"
    # JWT_SECRET must NOT be set: this deployment hardcodes JWT_ALGORITHM=RS256, and config.py
    # only reads/validates jwt_secret under HS256. A static placeholder secret in production IaC
    # is a live symmetric credential for a value the app never consults -- it must be absent, not
    # merely unused.
    assert not re.search(r'name\s*=\s*"JWT_SECRET"', tf_content), "JWT_SECRET must not be set when JWT_ALGORITHM=RS256"
    assert re.search(r'name\s*=\s*"REMOTE_WORKER_SECRET"', tf_content), "REMOTE_WORKER_SECRET is missing from Cloud Run"
    assert re.search(r'name\s*=\s*"REMOTE_INFERENCE_URL"', tf_content), "REMOTE_INFERENCE_URL is missing from Cloud Run"
    assert re.search(r'name\s*=\s*"METRICS_SCRAPE_TOKEN"', tf_content), "METRICS_SCRAPE_TOKEN is missing from Cloud Run"
    assert "cpu_idle = false" in tf_content, "Cloud Run should keep CPU allocated for WSS"
    assert "api_allow_unauthenticated" in tf_content, "Public invoker must be gated"
    assert "phone_wss_reachability" in tf_content, "Phone reachability check must be present"
    # PKI must not default into Terraform state for pilot/production.
    assert 'variable "manage_pki_in_terraform"' in tf_content
    manage_block = tf_content.split('variable "manage_pki_in_terraform"', 1)[1].split("variable ", 1)[0]
    assert "default     = false" in manage_block or "default = false" in manage_block
    
    # Test GPU Worker VM. The worker machine type / accelerator are selected by the
    # worker_use_gpu toggle (GPU is the default; a CPU VM is a supervised bench option), so
    # assert the GPU contract is present rather than a single hardcoded literal line.
    assert 'resource "google_compute_instance" "worker"' in tf_content, "GPU Worker instance is missing"
    assert '"g2-standard-4"' in tf_content, "GPU Worker default machine type g2-standard-4 is missing"
    assert re.search(r'type\s*=\s*"nvidia-l4"', tf_content), "GPU Worker is not using nvidia-l4 GPU"
    assert re.search(r'image\s*=\s*"cos-cloud/cos-stable"', tf_content), "GPU Worker is not using Container-Optimized OS"
    assert 'startup-script' in tf_content or 'cos-extensions install gpu' in tf_content, "GPU Worker startup script missing"
    # The GPU worker must remain the default so production does not silently deploy a CPU box.
    gpu_block = tf_content.split('variable "worker_use_gpu"', 1)[1].split("variable ", 1)[0]
    assert "default     = true" in gpu_block or "default = true" in gpu_block, "worker_use_gpu must default to true"

    assert 'resource "google_compute_firewall" "allow_iap_ssh"' in tf_content, "IAP SSH firewall must be in Terraform"
    assert "35.235.240.0/20" in tf_content, "IAP source range missing"

def test_iac_database_structure():
    tf_content = get_all_tf_content()
    assert 'resource "google_sql_database_instance" "postgres"' in tf_content, "Database instance is missing"
    assert 'tier = "db-custom-1-3840"' in tf_content, "Database is not using db-custom-1-3840"
    assert 'resource "google_sql_user" "db_user"' in tf_content, "Database user is missing"
    assert 'resource "random_password" "db_password"' in tf_content, "Database password generation is missing"

def test_iac_storage_structure():
    tf_content = get_all_tf_content()
    assert 'resource "google_storage_bucket" "diagnostics"' in tf_content, "Storage bucket is missing"
    assert 'age = 30' in tf_content, "Storage bucket lifecycle rule for 30 days is missing"

def test_iac_iam_structure():
    tf_content = get_all_tf_content()
    assert 'resource "google_secret_manager_secret_iam_member" "api_secret_accessor"' in tf_content, "API Secret IAM binding missing"
    assert 'resource "google_secret_manager_secret_iam_member" "worker_secret_accessor"' in tf_content, "Worker Secret IAM binding missing"
    assert 'resource "google_storage_bucket_iam_member" "api_storage_creator"' in tf_content, "Storage Creator IAM binding missing"
    assert 'roles/artifactregistry.reader' in tf_content, "Artifact Registry reader role missing"
