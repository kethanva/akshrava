#!/usr/bin/env bash
# Android ProtocolClient-shaped live E2E against Cloud Run (no handset required).
# Mirrors org.akshrava.app.ProtocolClient frame headers (mode/priority/trace_id/language).
# Usage: GOOGLE_APPLICATION_CREDENTIALS=... ./scripts/e2e_android_protocol_gcp.sh [device_id]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE_ID="${1:-android-proto-$(date +%s)}"
BASE_URL="${AKSHRAVA_BASE_URL:-https://akshrava-api-c7d3j4nzdq-uc.a.run.app}"
WSS_URL="${AKSHRAVA_WSS_URL:-${BASE_URL/https/wss}/v1/session}"
CALIBRATION_ID="${AKSHRAVA_CALIBRATION_ID:-e2e-r0}"

export PATH="${HOME}/google-cloud-sdk/bin:${PATH}"
: "${GOOGLE_APPLICATION_CREDENTIALS:?Set GOOGLE_APPLICATION_CREDENTIALS}"
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

echo "==> mint Android device token (${DEVICE_ID})"
gcloud secrets versions access latest --secret=akshrava-jwt-private >"${TMP}/jwt-private.pem"
chmod 600 "${TMP}/jwt-private.pem"
export JWT_ALGORITHM=RS256 JWT_PRIVATE_KEY_FILE="${TMP}/jwt-private.pem"
TOKEN="$("$PY" "${ROOT}/scripts/mint_device_token.py" "${DEVICE_ID}" --days 1)"

echo "==> ProtocolClient-shaped WSS session (${WSS_URL})"
export E2E_TOKEN="$TOKEN" E2E_WSS="$WSS_URL" E2E_CAL="$CALIBRATION_ID"
"$PY" - <<'PY'
import asyncio, base64, json, os, ssl, time
import certifi
import websockets

TOKEN = os.environ["E2E_TOKEN"]
URL = os.environ["E2E_WSS"]
CAL = os.environ["E2E_CAL"]
# Same valid 64x64 fixture used by e2e_gcp_pilot.sh
JPEG = base64.b64decode(
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
        assert ready.get("vision_enabled") is True, ready
        print("android_ready_ok detector=%s vision_enabled=%s" % (
            ready.get("detector"), ready.get("vision_enabled")))
        frame_id = 1
        capture_mono_ms = 100
        header = {
            "type": "frame",
            "id": frame_id,
            "capture_mono_ms": capture_mono_ms,
            "capture_epoch_ms": int(time.time() * 1000) - 40,
            "w": 64,
            "h": 64,
            "jpeg_bytes": len(JPEG),
            "camera_calibration_id": CAL,
            "pitch_cdeg": -1000,
            "roll_cdeg": 0,
            "pose_age_ms": 10,
            "mode": "normal",
            "priority": False,
            "language": "en-IN",
            "trace_id": "frame-%s-%s" % (frame_id, capture_mono_ms),
        }
        await ws.send(json.dumps(header))
        await ws.send(JPEG)
        result = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        assert result.get("type") == "result", result
        print("android_result_ok frame_id=%s hazard=%s inference_ms=%s" % (
            result.get("frame_id"), result.get("hazard"), result.get("server_inference_ms")))
        quality = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        assert quality.get("type") == "quality", quality
        print("android_quality_ok", quality)
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        assert pong.get("type") == "pong", pong
        print("android_ping_ok")
    print("ANDROID_PROTOCOL_E2E_PASS")

asyncio.run(main())
PY

echo "==> Android protocol E2E passed for ${DEVICE_ID}"
echo "    Debug APK default WSS: ${WSS_URL}"
echo "    Provision token via: ./scripts/print_android_pilot_provisioning.sh"
