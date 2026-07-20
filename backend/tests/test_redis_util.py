from akshrava_backend.redis_util import _ssl_kwargs_for_url


def test_plaintext_redis_url_adds_no_ssl_kwargs():
    assert _ssl_kwargs_for_url("redis://:pw@host:6379/0") == {}


def test_rediss_url_uses_ca_file_when_provided(monkeypatch):
    monkeypatch.setenv("REDIS_CA_CERT_FILE", "/run/secrets/redis/ca.pem")
    monkeypatch.delenv("REDIS_CA_PEM", raising=False)
    kwargs = _ssl_kwargs_for_url("rediss://:pw@10.0.0.5:6378/0")
    # Must be the string form, never an ssl.CERT_* enum (RedisSSLContext footgun documented in module).
    assert kwargs["ssl_cert_reqs"] == "required"
    assert kwargs["ssl_ca_certs"] == "/run/secrets/redis/ca.pem"
    assert kwargs["ssl_check_hostname"] is False
    assert "ssl_ca_data" not in kwargs


def test_rediss_url_falls_back_to_inline_ca_pem(monkeypatch):
    monkeypatch.delenv("REDIS_CA_CERT_FILE", raising=False)
    monkeypatch.setenv("REDIS_CA_PEM", "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----")
    kwargs = _ssl_kwargs_for_url("rediss://host:6378/0")
    assert kwargs["ssl_cert_reqs"] == "required"
    assert "ssl_ca_data" in kwargs
    assert "ssl_ca_certs" not in kwargs


def test_rediss_url_without_ca_keeps_pilot_up_with_insecure_fallback(monkeypatch):
    monkeypatch.delenv("REDIS_CA_CERT_FILE", raising=False)
    monkeypatch.delenv("REDIS_CA_PEM", raising=False)
    kwargs = _ssl_kwargs_for_url("rediss://host:6378/0")
    assert kwargs == {"ssl_check_hostname": False}
