import glob
import os
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


def get_tf_code():
    """Terraform content with `#` comments stripped.

    Needed for "this expression must NOT appear" assertions: a comment explaining why an old
    expression was removed otherwise reads as the expression still being present.
    """
    lines = []
    for line in get_all_tf_content().splitlines():
        stripped = line.split("#", 1)[0]
        if stripped.strip():
            lines.append(stripped)
    return "\n".join(lines)


def variable_block(name):
    """Return the body of one top-level `variable "<name>"` declaration.

    Splits on a newline-anchored `variable "` so prose inside a comment (e.g. the phrase
    "variable validation blocks") cannot truncate the block early.
    """
    content = get_all_tf_content()
    match = re.search(
        r'^variable\s+"%s"\s*\{(.*?)^\}' % re.escape(name),
        content,
        re.DOTALL | re.MULTILINE,
    )
    assert match, "variable %s is missing" % name
    return match.group(1)


def test_cloud_sql_declares_availability_explicitly_and_defaults_to_regional():
    """The provider default is ZONAL, so omitting availability_type buys a single-zone database.

    A zone event then takes Postgres down, /readyz fails on store.ping(), every API instance is
    removed from service, and assistance ends for every connected phone at once. The value must
    be stated in code (not inherited) and must default to REGIONAL.
    """
    tf_content = get_all_tf_content()
    assert "availability_type = var.database_availability_type" in tf_content, (
        "google_sql_database_instance must set availability_type explicitly"
    )
    block = variable_block("database_availability_type")
    assert re.search(r'default\s*=\s*"REGIONAL"', block), (
        "database_availability_type must default to REGIONAL"
    )


def test_redis_availability_is_not_decided_by_the_transit_encryption_toggle():
    """Availability and encryption are separate decisions and must stay separately expressible.

    These were once one control (`tier = var.redis_transit_encryption ? "STANDARD_HA" : "BASIC"`),
    so turning TLS off for a bench also removed failover from Redis -- which backs session
    admission, frame rate limiting and worker replay protection. Nobody disabling TLS intends
    that.
    """
    tf_code = get_tf_code()
    assert re.search(r"tier\s*=\s*var\.redis_tier", tf_code), (
        "google_redis_instance must take its tier from redis_tier"
    )
    assert 'var.redis_transit_encryption ? "STANDARD_HA"' not in tf_code, (
        "Redis tier must not be derived from the transit-encryption toggle"
    )
    block = variable_block("redis_tier")
    assert re.search(r'default\s*=\s*"STANDARD_HA"', block), (
        "redis_tier must default to the failover pair, not single-node BASIC"
    )


def test_redis_tls_on_basic_tier_is_rejected_at_plan_time():
    """BASIC cannot terminate TLS; fail with an actionable message rather than an API error."""
    tf_code = get_tf_code()
    assert "precondition" in tf_code, "Redis TLS/tier invariant must be enforced by a precondition"
    assert 'var.redis_transit_encryption && var.redis_tier == "BASIC"' in tf_code, (
        "the TLS-requires-STANDARD_HA invariant is missing"
    )


def test_availability_variable_descriptions_match_their_actual_defaults():
    """A stale docstring on an availability control is how an operator believes they have HA.

    enable_worker_ha shipped with `default = true` while its own description said "Default false",
    which is exactly the kind of drift that makes a reviewer (or an on-call engineer) reason about
    the wrong topology. Assert the two agree.
    """
    for name in ("enable_worker_ha", "redis_tier", "database_availability_type"):
        block = variable_block(name)
        default = re.search(r"default\s*=\s*(\S+)", block)
        assert default, "variable %s has no default" % name
        value = default.group(1).strip('"')
        description = re.search(r'description\s*=\s*"(.*?)"\s*$', block, re.DOTALL | re.MULTILINE)
        assert description, "variable %s has no description" % name
        text = description.group(1).lower()
        contradiction = "default %s" % ("true" if value == "false" else "false")
        assert contradiction not in text, (
            "variable %s has default=%s but its description claims '%s'"
            % (name, value, contradiction)
        )


def test_worker_ha_defaults_on_because_a_single_worker_is_a_fleet_wide_spof():
    """One worker VM silences every connected phone at once when its host has an event.

    The cheaper single-VM bench posture must be the option that requires a deliberate opt-out.
    """
    block = variable_block("enable_worker_ha")
    assert re.search(r"default\s*=\s*true", block), "enable_worker_ha must default to true"


def test_api_deployment_can_configure_otlp_export_without_embedding_credentials():
    """A configured collector must reach an image that actually includes the exporter."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    with open(os.path.join(root, "backend", "Dockerfile")) as handle:
        dockerfile = handle.read()
    assert ".[otel]" in dockerfile, "API image must install the OTLP exporter extra"

    tf_content = get_all_tf_content()
    block = variable_block("otlp_exporter_endpoint")
    assert re.search(r'default\s*=\s*""', block)
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in tf_content


def test_phone_delivery_metrics_are_exported_as_aggregate_log_metrics_with_an_alert():
    tf_content = get_all_tf_content()
    for name in (
        "akshrava_results_sent",
        "akshrava_result_acknowledgements_expected",
        "akshrava_phone_results_acknowledged",
        "akshrava_phone_results_acknowledged_fresh",
        "akshrava_phone_results_acknowledged_missing",
    ):
        assert name in tf_content
    assert 'resource "google_monitoring_alert_policy" "phone_result_ack_missing"' in tf_content

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
