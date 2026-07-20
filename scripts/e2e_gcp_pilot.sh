#!/usr/bin/env bash
# Live GCP pilot E2E against Cloud Run (+ optional remote worker).
# Usage: ./scripts/e2e_gcp_pilot.sh [device_id]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE_ID="${1:-e2e-pilot-$(date +%s)}"
BASE_URL="${AKSHRAVA_BASE_URL:-https://akshrava-api-c7d3j4nzdq-uc.a.run.app}"
WSS_URL="${AKSHRAVA_WSS_URL:-${BASE_URL/https/wss}/v1/session}"

export PATH="${HOME}/google-cloud-sdk/bin:${PATH}"
: "${GOOGLE_APPLICATION_CREDENTIALS:?Set GOOGLE_APPLICATION_CREDENTIALS to the deploy SA JSON}"
: "${CLOUDSDK_CORE_PROJECT:=project-704ccb8e-8b12-4da6-a3f}"
export CLOUDSDK_CORE_PROJECT CLOUDSDK_CORE_DISABLE_PROMPTS=1
export CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="${GOOGLE_APPLICATION_CREDENTIALS}"

if [[ -x "${ROOT}/backend/.venv/bin/python" ]]; then
  PY="${ROOT}/backend/.venv/bin/python"
else
  PY="python3"
fi
"$PY" -c "import jwt, websockets, certifi" 2>/dev/null || \
  "$PY" -m pip install -q 'PyJWT>=2.8' 'websockets>=12' 'certifi'

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> livez"
curl -fsS -m 20 "${BASE_URL}/livez" | tee "${TMP}/livez.json"
echo
echo "==> readyz"
curl -fsS -m 20 "${BASE_URL}/readyz" | tee "${TMP}/readyz.json"
echo

echo "==> mint device token (${DEVICE_ID})"
gcloud secrets versions access latest --secret=akshrava-jwt-private >"${TMP}/jwt-private.pem"
chmod 600 "${TMP}/jwt-private.pem"
export JWT_ALGORITHM=RS256 JWT_PRIVATE_KEY_FILE="${TMP}/jwt-private.pem"
TOKEN="$("$PY" "${ROOT}/scripts/mint_device_token.py" "${DEVICE_ID}" --days 1)"
echo "token_len=${#TOKEN}"

echo "==> websocket session + frame round-trip"
export E2E_TOKEN="$TOKEN" E2E_WSS="$WSS_URL" E2E_BASE="$BASE_URL" E2E_DEVICE="$DEVICE_ID"
"$PY" - <<'PY'
import asyncio, base64, json, os, ssl, time, urllib.request
import certifi
import websockets

TOKEN = os.environ["E2E_TOKEN"]
URL = os.environ["E2E_WSS"]
BASE = os.environ["E2E_BASE"]
DEVICE = os.environ["E2E_DEVICE"]
JPEG = base64.b64decode(
    # Valid 64x64 JPEG (solid + red square). Prior 1x1 stub was corrupt and crashed PIL on the worker.
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/"
    "2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh7/wAARCABAAEADASIAAhEBAxEB/"
    "8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAk"
    "M2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2"
    "t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQD"
    "BAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm"
    "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/"
    "9oADAMBAAIRAxEAPwDmqKKK/VT80CiiigAooooAKKKKACiiigBRRigUtfl3EfEeZYPMqlChUtFWsrRe8U+qvufunBvBuS5lktHFYqjzTlzXfNJbSklop"
    "JbJdBMUGlpDRw5xHmWMzKnQr1Lxd7q0VtFvor7hxlwbkuW5LWxWFo8s48tnzSe8op6OTWzfQSiiiv1E/CwooooAUUtIKM1+XcR8OZljMyqV6FO8XazvFb"
    "RS6u+5+6cG8ZZLluS0cLiq3LOPNdcsnvKTWqi1s11FpDRmg0cOcOZlg8yp169O0Ve7vF7xa6O+4cZcZZLmWS1sLha3NOXLZcsltKLerilsn1Eooor9RPws"
    "KKKKACiiigAooooAKKKKAP/Z"
)
ctx = ssl.create_default_context(cafile=certifi.where())

async def main():
    async with websockets.connect(
        URL, open_timeout=45, close_timeout=15, max_size=2_000_000, ssl=ctx,
        extra_headers={"Authorization": f"Bearer {TOKEN}"},
    ) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))
        assert ready.get("type") == "ready", ready
        vision = bool(ready.get("vision_enabled"))
        print("ready_ok detector=%s vision_enabled=%s" % (ready.get("detector"), vision))
        header = {
            "type": "frame", "id": 1, "capture_mono_ms": 100,
            "capture_epoch_ms": int(time.time() * 1000) - 40,
            "w": 64, "h": 64, "jpeg_bytes": len(JPEG),
            "camera_calibration_id": "e2e-r0",
            "pitch_cdeg": -1000, "roll_cdeg": 0, "pose_age_ms": 10, "language": "en",
        }
        await ws.send(json.dumps(header))
        await ws.send(JPEG)
        result = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        assert result.get("type") == "result", result
        print("result_ok frame_id=%s hazard=%s inference_ms=%s" % (
            result.get("frame_id"), result.get("hazard"), result.get("server_inference_ms")))
        quality = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        assert quality.get("type") == "quality", quality
        print("quality_ok", quality)
        req = urllib.request.Request(
            f"{BASE}/v1/devices/{DEVICE}/events",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            body = json.loads(resp.read().decode())
            assert resp.status == 200
            print("events_ok count=%s" % len(body.get("events", [])))
        # Reject missing auth
        bad = urllib.request.Request(f"{BASE}/v1/devices/{DEVICE}/events")
        try:
            urllib.request.urlopen(bad, timeout=15, context=ctx)
            raise SystemExit("expected 401 without token")
        except urllib.error.HTTPError as exc:
            assert exc.code in (401, 403), exc.code
            print("auth_reject_ok status=%s" % exc.code)
    print("E2E_PASS vision_enabled=%s" % vision)

asyncio.run(main())
PY

echo "==> all e2e checks passed for ${DEVICE_ID}"
