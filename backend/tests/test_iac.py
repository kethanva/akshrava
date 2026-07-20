import os
import subprocess
import pytest

IAC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../gcp"))

def has_terraform():
    try:
        subprocess.run(["terraform", "--version"], check=True, capture_output=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

@pytest.mark.skipif(not has_terraform(), reason="Terraform is not installed")
def test_terraform_formatting():
    """Ensure all Terraform files are properly formatted."""
    result = subprocess.run(
        ["terraform", "fmt", "-check"],
        cwd=IAC_DIR,
        capture_output=True,
        text=True,
        env={**os.environ, "TF_CLI_CONFIG_FILE": "/dev/null"}
    )
    assert result.returncode == 0, f"Terraform format check failed: {result.stdout}\n{result.stderr}"

@pytest.mark.skipif(not has_terraform(), reason="Terraform is not installed")
def test_terraform_validation():
    """Ensure Terraform code is valid (requires terraform init)."""
    # Attempt to init in memory/backend false. If it fails (e.g. network/sandbox), we skip.
    init = subprocess.run(
        ["terraform", "init", "-backend=false"],
        cwd=IAC_DIR,
        capture_output=True,
        text=True,
        env={**os.environ, "TF_CLI_CONFIG_FILE": "/dev/null"}
    )
    if init.returncode != 0:
        pytest.skip(f"Terraform init failed, skipping validation. Error: {init.stderr}")

    result = subprocess.run(
        ["terraform", "validate"],
        cwd=IAC_DIR,
        capture_output=True,
        text=True,
        env={**os.environ, "TF_CLI_CONFIG_FILE": "/dev/null"}
    )
    assert result.returncode == 0, f"Terraform validation failed: {result.stdout}\n{result.stderr}"
