import pytest

from akshrava_backend.config import Settings


def test_dev_auth_bypass_is_rejected_for_pilot_environment(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "pilot")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    with pytest.raises(ValueError, match="DEV_AUTH_BYPASS"):
        Settings.from_env()


def test_development_can_explicitly_use_the_local_test_bypass(monkeypatch):
    monkeypatch.setenv("AKSHRAVA_ENV", "development")
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    assert Settings.from_env().dev_auth_bypass is True
