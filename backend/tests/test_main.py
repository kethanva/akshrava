import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from akshrava_backend.main import app, _retention_loop, _renew_or_readmit, store

# The suite runs with DEV_AUTH_BYPASS=true, so auth.device_claims_from_token accepts exactly this
# literal and maps it to this device id. Minting a real JWT here would need exp/sub/aud to match
# the server's require list, which is covered in test_auth.py instead.
DEV_TOKEN = "dev-device-token"
DEV_DEVICE_ID = "dev-device"


def test_livez_and_healthz_endpoints():
    with TestClient(app) as client:
        res_livez = client.get("/livez")
        assert res_livez.status_code == 200
        assert res_livez.json()["ok"] is True

        res_healthz = client.get("/healthz")
        assert res_healthz.status_code == 200
        assert res_healthz.json()["ok"] is True


def test_readyz_success_and_failure():
    with TestClient(app) as client:
        # Success path
        res = client.get("/readyz")
        assert res.status_code == 200
        assert res.json()["ok"] is True

        # Failure path: store.ping raises Exception
        with patch.object(store, "ping", side_effect=RuntimeError("DB Down")):
            res_fail = client.get("/readyz")
            assert res_fail.status_code == 503
            assert res_fail.json()["detail"] == "dependencies unavailable"


def test_prometheus_metrics_endpoint():
    with TestClient(app) as client:
        res = client.get("/metrics")
        assert res.status_code == 200
        assert "akshrava" in res.text


def test_prometheus_metrics_requires_token_outside_dev(patched_settings):
    # `settings` is a frozen dataclass shared by the whole app, so the restore has to survive a
    # failing assertion — otherwise one broken test leaves the suite in "pilot" and every later
    # metrics/auth test fails for a reason unrelated to what it covers.
    patched_settings(environment="pilot", metrics_scrape_token="secret-token")

    with TestClient(app) as client:
        # Unauthenticated
        assert client.get("/metrics").status_code == 404

        # Invalid token
        assert client.get("/metrics", headers={"X-Akshrava-Metrics-Token": "wrong"}).status_code == 404

        # Valid token via header
        res_header = client.get("/metrics", headers={"X-Akshrava-Metrics-Token": "secret-token"})
        assert res_header.status_code == 200

        # Valid token via bearer
        res_bearer = client.get("/metrics", headers={"Authorization": "Bearer secret-token"})
        assert res_bearer.status_code == 200


@pytest.mark.asyncio
async def test_retention_loop():
    stop_event = asyncio.Event()

    # Test retention loop running once then stopping
    async def set_stop():
        await asyncio.sleep(0.05)
        stop_event.set()

    with patch.object(store, "purge_alert_events_older_than", AsyncMock(return_value=5)) as mock_purge:
        await asyncio.gather(_retention_loop(stop_event), set_stop())
        mock_purge.assert_called()


@pytest.mark.asyncio
async def test_renew_or_readmit():
    with patch("akshrava_backend.main.session_admission.renew", AsyncMock(return_value=True)):
        assert await _renew_or_readmit("sess-1") is True

    with patch("akshrava_backend.main.session_admission.renew", AsyncMock(return_value=False)), \
         patch("akshrava_backend.main.session_admission.try_open", AsyncMock(return_value=True)):
        assert await _renew_or_readmit("sess-2") is True


def test_device_events_endpoint():
    with TestClient(app) as client:
        # Missing token
        res_401 = client.get("/v1/devices/dev-other/events")
        assert res_401.status_code == 401

        # Valid token, but for a different device: the feed is device-scoped, never cross-device.
        res_mismatch = client.get(
            "/v1/devices/dev-other/events", headers={"Authorization": f"Bearer {DEV_TOKEN}"}
        )
        assert res_mismatch.status_code == 403

        # Revoked device
        with patch.object(store, "is_device_revoked", AsyncMock(return_value=True)):
            res_revoked = client.get(
                f"/v1/devices/{DEV_DEVICE_ID}/events",
                headers={"Authorization": f"Bearer {DEV_TOKEN}"},
            )
            assert res_revoked.status_code == 403

        # Allowed device returns its own event feed
        with patch.object(store, "is_device_revoked", AsyncMock(return_value=False)), \
             patch.object(store, "recent_events", AsyncMock(return_value=[])):
            res_ok = client.get(
                f"/v1/devices/{DEV_DEVICE_ID}/events",
                headers={"Authorization": f"Bearer {DEV_TOKEN}"},
            )
            assert res_ok.status_code == 200
            assert res_ok.json() == {"events": []}


def test_websocket_rejects_invalid_token():
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/v1/session?token=not-a-real-token"):
                pass
        assert exc.value.code == 4401


def test_websocket_revoked_device_rejection():
    # Asserting the close code matters: every rejection path here disconnects, so a bare
    # `raises(Exception)` would pass even when the socket died for an unrelated reason.
    with TestClient(app) as client:
        with patch.object(store, "is_device_revoked", AsyncMock(return_value=True)):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect(f"/v1/session?token={DEV_TOKEN}"):
                    pass
            assert exc.value.code == 4403


def test_websocket_session_admission_rejection():
    with TestClient(app) as client:
        with patch.object(store, "is_device_revoked", AsyncMock(return_value=False)), \
             patch("akshrava_backend.main.session_admission.try_open", AsyncMock(return_value=False)):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect(f"/v1/session?token={DEV_TOKEN}"):
                    pass
            assert exc.value.code == 1013
