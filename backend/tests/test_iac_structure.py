import os
import glob
import re

IAC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../gcp"))

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
    assert re.search(r'name\s*=\s*"JWT_SECRET"', tf_content), "JWT_SECRET is missing from Cloud Run"
    assert re.search(r'name\s*=\s*"REMOTE_WORKER_SECRET"', tf_content), "REMOTE_WORKER_SECRET is missing from Cloud Run"
    assert re.search(r'name\s*=\s*"REMOTE_INFERENCE_URL"', tf_content), "REMOTE_INFERENCE_URL is missing from Cloud Run"
    
    # Test GPU Worker VM
    assert 'resource "google_compute_instance" "worker"' in tf_content, "GPU Worker instance is missing"
    assert 'machine_type = "g2-standard-4"' in tf_content, "GPU Worker is not using g2-standard-4"
    assert 'type  = "nvidia-l4"' in tf_content, "GPU Worker is not using nvidia-l4 GPU"
    assert 'image = "cos-cloud/cos-stable"' in tf_content, "GPU Worker is not using Container-Optimized OS"
    assert 'startup-script' in tf_content or 'cos-extensions install gpu' in tf_content, "GPU Worker startup script missing"

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
