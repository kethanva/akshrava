import glob
import os
import re
from pathlib import Path

IAC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../cloud/gcp"))
REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo_text(*parts: str) -> str:
    return REPO_ROOT.joinpath(*parts).read_text(encoding="utf-8")


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


def test_worker_autohealing_checks_application_readiness_not_only_an_open_port():
    """A wedged detector keeps Caddy listening, so a TCP check would leave it in service forever."""
    tf_content = get_all_tf_content()
    assert 'http_health_check {' in tf_content
    assert 'request_path = "/readyz"' in tf_content
    assert 'ports    = ["8001"]' in tf_content
    startup = os.path.join(IAC_DIR, "scripts", "worker-startup.sh.tftpl")
    with open(startup) as handle:
        script = handle.read()
    assert "-p 8001:8000" in script
    assert "130.211.0.0/22 35.191.0.0/16" in script


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


def test_api_transport_and_instance_concurrency_bound_websocket_buffering():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    with open(os.path.join(root, "backend", "Dockerfile")) as handle:
        dockerfile = handle.read()
    assert "akshrava_backend.gunicorn_worker.BoundedUvicornWorker" in dockerfile
    with open(os.path.join(root, "backend", "akshrava_backend", "gunicorn_worker.py")) as handle:
        worker = handle.read()
    assert '"ws_max_size": 1_048_576' in worker
    assert '"ws_max_queue": 8' in worker
    assert "max_instance_request_concurrency = 20" in get_all_tf_content()


def test_deployment_never_widens_the_shared_result_freshness_ceiling():
    """Slow CPU inference is diagnostic-only once it exceeds the 2.5 s phone speech budget."""
    app_path = os.path.join(IAC_DIR, "app.tf")
    with open(app_path) as handle:
        app = handle.read()
    alert_age = re.search(
        r'name\s*=\s*"ALERT_MAX_AGE_MS"(.*?)^\s*\}',
        app,
        re.DOTALL | re.MULTILINE,
    )
    assert alert_age, "ALERT_MAX_AGE_MS environment block is missing"
    assert re.search(r'value\s*=\s*"2500"', alert_age.group(1)), (
        "all deployment profiles must keep the shared 2.5 s freshness ceiling"
    )


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


def test_api_service_declares_a_startup_probe_on_readyz():
    tf_content = get_all_tf_content()
    assert "startup_probe" in tf_content
    assert re.search(r'path\s*=\s*"/readyz"', tf_content)
    assert "liveness_probe" in tf_content
    assert re.search(r'path\s*=\s*"/livez"', tf_content)


def test_every_alert_policy_declares_notification_channels():
    tf_content = get_all_tf_content()
    assert tf_content.count("notification_channels = var.monitoring_notification_channels") >= 3


def test_production_requires_a_notification_channel():
    tf_content = get_all_tf_content()
    assert "monitoring_notification_channels" in tf_content
    assert 'var.environment != "production"' in tf_content or "environment != \"production\"" in tf_content
    assert "notificationChannels" in _repo_text("scripts", "gcp_preflight.sh")


def test_database_deletion_protection_is_on_outside_development():
    tf_content = get_tf_code()
    assert 'deletion_protection = var.environment != "development"' in tf_content


def test_worker_deadline_check_references_the_api_remote_timeout():
    tf_content = get_all_tf_content()
    assert 'check "worker_deadline_below_api_deadline"' in tf_content
    assert "remote_inference_timeout_ms" in tf_content
    assert "worker_infer_timeout_seconds" in tf_content


def test_migrate_then_deploy_targets_the_job_before_the_full_apply():
    script = _repo_text("scripts", "gcp_migrate_then_deploy.sh")
    assert "-target=google_cloud_run_v2_job.migrate" in script
    job_idx = script.index("gcloud run jobs execute akshrava-migrate")
    full_apply_idx = script.rindex("terraform")
    assert job_idx < full_apply_idx
    assert script.index("-target=google_cloud_run_v2_job.migrate") < job_idx


def test_operations_md_exists_and_covers_the_referenced_sections():
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../OPERATIONS.md"))
    assert os.path.isfile(path)
    text = _repo_text("OPERATIONS.md")
    for heading in (
        "Current pilot configuration",
        "Migrate before traffic",
        "Rollback",
        "Revoke a device",
        "Rotate the JWT signing key",
        "Who is paged",
        "Remote state",
        "Expected metric shifts after this release",
    ):
        assert heading in text, heading


def test_ci_runs_the_ios_test_steps_on_main_pushes():
    ci = _repo_text(".github", "workflows", "ci.yml")
    assert "\n  ios:\n    if: github.event_name == 'pull_request'" not in ci
    assert "./scripts/test_ios.sh" in ci
    assert "Build the unsigned archive artifact" in ci
    archive_block = ci.split("Build the unsigned archive artifact", 1)[1]
    assert "if: github.event_name == 'pull_request'" in archive_block.split("run:", 1)[0]


def test_ci_runs_android_unit_tests_on_main_pushes():
    ci = _repo_text(".github", "workflows", "ci.yml")
    assert "\n  android:\n    if: github.event_name == 'pull_request'" not in ci
    assert ":app:testDebugUnitTest" in ci


def test_compose_gpu_worker_deadline_is_below_api_remote():
    compose = _repo_text("cloud", "local", "docker-compose.yml")
    remote_ms = float(re.search(r"REMOTE_INFERENCE_TIMEOUT_MS:-\s*(\d+)", compose).group(1))
    worker_s = float(re.search(r"WORKER_INFER_TIMEOUT_SECONDS:-\s*([0-9.]+)", compose).group(1))
    assert worker_s * 1000 < remote_ms


