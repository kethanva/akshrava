import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from akshrava_backend.domain import FrameHeader, SessionState
from akshrava_backend.main import (
    _analyze_and_reply,
    app,
    session_application,
    store,
    _retention_loop,
    _renew_or_readmit,
)

# The suite runs with DEV_AUTH_BYPASS=true, so auth.device_claims_from_token accepts exactly this
# literal and maps it to this device id. Minting a real JWT here would need exp/sub/aud to match
# the server's require list, which is covered in test_auth.py instead.
DEV_TOKEN = "dev-device-token"
DEV_DEVICE_ID = "dev-device"


def _result_for_ack_ordering() -> dict:
    return {
        "type": "result",
        "frame_id": 7,
        "capture_mono_ms": 1000,
        "server_inference_ms": 1,
        "server_received_epoch_ms": 1001,
        "pipeline_stage_ms": {},
        "hazard": None,
    }


def _header_for_ack_ordering() -> FrameHeader:
    return FrameHeader(
        frame_id=7,
        capture_mono_ms=1000,
        capture_epoch_ms=None,
        width=1,
        height=1,
        jpeg_bytes=1,
        calibration_id="",
        pitch_cdeg=None,
        roll_cdeg=None,
        pose_age_ms=None,
        mode="normal",
    )


@pytest.mark.asyncio
async def test_result_ack_slot_is_registered_before_result_write():
    """A phone may acknowledge while send_json yields to the receive loop."""
    handler = MagicMock(result_acknowledgement_supported=True)
    websocket = MagicMock()

    async def send_json(payload):
        if payload["type"] == "result":
            handler.note_result_sent.assert_called_once_with(7, True)

    websocket.send_json = AsyncMock(side_effect=send_json)
    with patch.object(session_application, "analyze_frame", AsyncMock(return_value=_result_for_ack_ordering())):
        await _analyze_and_reply(
            websocket,
            SessionState(device_id=DEV_DEVICE_ID),
            _header_for_ack_ordering(),
            b"x",
            1,
            DEV_DEVICE_ID,
            asyncio.Lock(),
            handler,
            True,
        )

    handler.forget_result_sent.assert_not_called()


@pytest.mark.asyncio
async def test_failed_result_write_forgets_pre_registered_ack_slot():
    handler = MagicMock(result_acknowledgement_supported=True)
    websocket = MagicMock()
    websocket.send_json = AsyncMock(side_effect=RuntimeError("peer gone"))

    with patch.object(session_application, "analyze_frame", AsyncMock(return_value=_result_for_ack_ordering())):
        with pytest.raises(RuntimeError, match="peer gone"):
            await _analyze_and_reply(
                websocket,
                SessionState(device_id=DEV_DEVICE_ID),
                _header_for_ack_ordering(),
                b"x",
                1,
                DEV_DEVICE_ID,
                asyncio.Lock(),
                handler,
                True,
            )

    handler.note_result_sent.assert_called_once_with(7, True)
    handler.forget_result_sent.assert_called_once_with(7)


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


def test_phone_delivery_window_logs_only_aggregate_delta_metrics():
    from akshrava_backend import main as main_mod
    from akshrava_backend.metrics import Metrics

    isolated = Metrics()
    isolated.result_sent(acknowledgement_expected=True)
    isolated.result_sent(acknowledgement_expected=True)
    isolated.phone_result_acknowledged(fresh=True)
    with patch.object(main_mod, "metrics", isolated), patch.object(main_mod.logger, "info") as info:
        main_mod._log_phone_delivery_window()
        info.assert_called_once()
        extra = info.call_args.kwargs["extra"]
        assert extra == {
            "event": "phone_delivery_window",
            "results_sent": 2,
            "result_acknowledgements_expected": 2,
            "phone_results_acknowledged": 1,
            "phone_results_acknowledged_fresh": 1,
            # The second result is still in flight, NOT missing. This field was previously
            # derived as `expected - acknowledged` and reported 1 here -- a phantom failure for
            # a perfectly healthy session, scaling with fleet size and permanently tripping the
            # delivery alert. Only an explicit eviction counts as missing now.
            "phone_results_acknowledged_missing": 0,
        }
        # Deltas are consumed, so a quiet period does not create a high-volume log stream.
        main_mod._log_phone_delivery_window()
        info.assert_called_once()


def test_phone_delivery_window_reports_a_genuinely_unacknowledged_result():
    from akshrava_backend import main as main_mod
    from akshrava_backend.metrics import Metrics

    isolated = Metrics()
    isolated.result_sent(acknowledgement_expected=True)
    isolated.phone_result_unacknowledged()
    with patch.object(main_mod, "metrics", isolated), patch.object(main_mod.logger, "info") as info:
        main_mod._log_phone_delivery_window()
        assert info.call_args.kwargs["extra"]["phone_results_acknowledged_missing"] == 1


@pytest.mark.asyncio
async def test_retention_loop():
    stop_event = asyncio.Event()

    # Test retention loop running once then stopping
    async def set_stop():
        await asyncio.sleep(0.05)
        stop_event.set()

    with patch.object(store, "purge_alert_events_if_leader", AsyncMock(return_value=5)) as mock_purge:
        await asyncio.gather(_retention_loop(stop_event), set_stop())
        mock_purge.assert_called()


@pytest.mark.asyncio
async def test_retention_loop_goes_through_the_leader_elected_path_not_the_raw_purge():
    """Every API instance runs this loop; only the advisory-lock holder may delete.

    Calling purge_alert_events_older_than directly here would put N Cloud Run instances into the
    same batched DELETE concurrently. Pin the call so that regression is caught.
    """
    stop_event = asyncio.Event()

    async def set_stop():
        await asyncio.sleep(0.05)
        stop_event.set()

    with patch.object(store, "purge_alert_events_if_leader", AsyncMock(return_value=None)) as leader, \
         patch.object(store, "purge_alert_events_older_than", AsyncMock(return_value=99)) as raw:
        await asyncio.gather(_retention_loop(stop_event), set_stop())

    leader.assert_called()
    raw.assert_not_called()


@pytest.mark.asyncio
async def test_retention_loop_survives_a_failing_purge():
    """A retention failure must be logged and retried later, never crash a live session process."""
    stop_event = asyncio.Event()

    async def set_stop():
        await asyncio.sleep(0.05)
        stop_event.set()

    with patch.object(
        store, "purge_alert_events_if_leader", AsyncMock(side_effect=RuntimeError("db gone"))
    ) as mock_purge:
        # Must not raise out of the loop.
        await asyncio.gather(_retention_loop(stop_event), set_stop())

    mock_purge.assert_called()


@pytest.mark.asyncio
async def test_lifespan_does_not_block_startup_on_a_retention_purge():
    """Startup must not await the purge: it walks unbounded 5000-row batches.

    Awaiting it before yielding made every cold start and scale-up event pay for a full retention
    sweep before the instance could accept its first WebSocket -- a startup-deadline risk on the
    exact autoscaling path that exists to absorb load. The loop still purges immediately on its
    own first iteration, so nothing is skipped.
    """
    import inspect

    from akshrava_backend import main as main_mod

    source = inspect.getsource(main_mod.lifespan)
    before_yield = source.split("yield")[0]
    assert "purge_alert_events" not in before_yield, (
        "lifespan must not await a retention purge before yielding"
    )


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
