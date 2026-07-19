import io
import base64
import hashlib
import hmac
import json
import secrets
import ssl
import time
from threading import Lock
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

from .domain import Detection


class Detector(ABC):
    @abstractmethod
    def detect(self, jpeg: bytes) -> List[Detection]:
        raise NotImplementedError

    def requires_serial_execution(self) -> bool:
        """Whether one shared instance must process frames one at a time.

        The conservative default protects model instances and detectors that retain post-detect
        state. Stateless remote adapters override it so the control plane can keep multiple
        phones from waiting behind a network round trip.
        """
        return True

    def detect_batch(self, jpegs: List[bytes]) -> List[List[Detection]]:
        """Detect a bounded group of independent frames.

        Adapters that cannot batch safely retain correct behaviour through this default. GPU
        runtimes override it so the worker can coalesce a short burst without coupling phone
        sessions or making the control plane hold a global inference lock.
        """
        return [self.detect(jpeg) for jpeg in jpegs]


def jpeg_dimensions(jpeg: bytes):
    """Validate that untrusted bytes are a bounded JPEG before a detector sees them."""
    try:
        with Image.open(io.BytesIO(jpeg)) as image:
            if image.format != "JPEG":
                raise ValueError("frame is not JPEG")
            dimensions = image.size
            image.verify()
        return dimensions
    except Exception as exc:
        raise ValueError("invalid JPEG") from exc


class NoopDetector(Detector):
    """Safe default: accepts frames but never invents an alert."""

    def detect(self, jpeg: bytes) -> List[Detection]:
        return []

    def requires_serial_execution(self) -> bool:
        return False


class RemoteInferenceError(RuntimeError):
    """The trusted GPU worker cannot produce a safely usable result."""


class RemoteWorkerDetector(Detector):
    """Synchronous, authenticated adapter for a private GPU inference worker.

    The phone still speaks only to the control plane.  The worker gets an individual JPEG plus a
    short-lived HMAC, then returns bounded boxes; it never sees a device token, session state,
    alert history, or any path back to the phone.
    """

    _MAX_RESPONSE_BYTES = 256_000
    _MAX_DETECTIONS = 100

    def __init__(
        self,
        endpoint: str,
        shared_secret: str,
        timeout_ms: int,
        tls_ca_file: str = "",
        tls_client_cert_file: str = "",
        tls_client_key_file: str = "",
    ):
        self.endpoint = endpoint
        self.shared_secret = shared_secret.encode("utf-8")
        self.timeout_seconds = timeout_ms / 1000.0
        self._ssl_context = None
        if tls_ca_file:
            self._ssl_context = ssl.create_default_context(cafile=tls_ca_file)
            self._ssl_context.load_cert_chain(tls_client_cert_file, tls_client_key_file)

    def detect(self, jpeg: bytes) -> List[Detection]:
        body = json.dumps(
            {"image_b64": base64.b64encode(jpeg).decode("ascii")},
            separators=(",", ":"),
        ).encode("utf-8")
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        signature = hmac.new(
            self.shared_secret,
            timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Akshrava-Timestamp": timestamp,
                "X-Akshrava-Nonce": nonce,
                "X-Akshrava-Signature": signature,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds, context=self._ssl_context) as response:
                raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RemoteInferenceError("remote worker unavailable") from exc
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise RemoteInferenceError("remote worker response too large")
        try:
            payload = json.loads(raw)
            items = payload["detections"]
            if not isinstance(items, list) or len(items) > self._MAX_DETECTIONS:
                raise ValueError("invalid detections")
            return [self._parse_detection(item) for item in items]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RemoteInferenceError("invalid remote worker response") from exc

    def requires_serial_execution(self) -> bool:
        return False

    @staticmethod
    def _parse_detection(item) -> Detection:
        if not isinstance(item, dict):
            raise ValueError("detection must be an object")
        label = item.get("label")
        confidence = item.get("confidence")
        box = item.get("box")
        if not isinstance(label, str) or not 0 < len(label) <= 128:
            raise ValueError("invalid label")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("invalid confidence")
        if not 0 <= float(confidence) <= 1:
            raise ValueError("confidence out of range")
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("invalid box")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in box):
            raise ValueError("invalid box value")
        parsed_box = tuple(float(value) for value in box)
        if parsed_box[2] < parsed_box[0] or parsed_box[3] < parsed_box[1]:
            raise ValueError("invalid box order")
        return Detection(label=label, confidence=float(confidence), box=parsed_box)


class FailoverRemoteWorkerDetector(Detector):
    """Stable round-robin worker selection with immediate failover to configured warm peers."""

    def __init__(self, endpoints, shared_secret: str, timeout_ms: int, **tls):
        self._workers = [RemoteWorkerDetector(endpoint, shared_secret, timeout_ms, **tls) for endpoint in endpoints]
        self._next = 0
        self._lock = Lock()

    def detect(self, jpeg: bytes) -> List[Detection]:
        with self._lock:
            start = self._next
            self._next = (self._next + 1) % len(self._workers)
        errors = []
        for offset in range(len(self._workers)):
            worker = self._workers[(start + offset) % len(self._workers)]
            try:
                return worker.detect(jpeg)
            except RemoteInferenceError as exc:
                errors.append(exc)
        raise RemoteInferenceError("all configured remote workers are unavailable") from errors[-1]

    def requires_serial_execution(self) -> bool:
        return False


class UltralyticsDetector(Detector):
    """Optional detector. Enable only after licence/weights review and benchmarking."""

    def __init__(self, weights: str):
        if not Path(weights).is_file():
            raise RuntimeError("YOLO_WEIGHTS must name a local, baked-in or read-only mounted model file")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("install backend[yolo] to enable DETECTOR=ultralytics") from exc
        self._model = YOLO(weights)

    def detect(self, jpeg: bytes) -> List[Detection]:
        return self.detect_batch([jpeg])[0]

    def detect_batch(self, jpegs: List[bytes]) -> List[List[Detection]]:
        images = [Image.open(io.BytesIO(jpeg)).convert("RGB") for jpeg in jpegs]
        results = self._model.predict(images, imgsz=640, conf=0.25, verbose=False)
        parsed = []
        for result in results:
            names = result.names
            detections = []
            if result.boxes is not None:
                for box in result.boxes:
                    xyxy = [float(item) for item in box.xyxy[0].tolist()]
                    confidence = float(box.conf[0].item())
                    class_id = int(box.cls[0].item())
                    detections.append(
                        Detection(label=str(names[class_id]), confidence=confidence, box=tuple(xyxy))
                    )
            parsed.append(detections)
        return parsed


def make_detector(
    kind: str,
    weights: str,
    cloud_provider=None,
    cloud_min_confidence: float = 0.55,
    remote_inference_url: str = "",
    remote_worker_secret: str = "",
    remote_inference_timeout_ms: int = 450,
    remote_tls_ca_file: str = "",
    remote_tls_client_cert_file: str = "",
    remote_tls_client_key_file: str = "",
) -> Detector:
    if kind == "noop":
        detector = NoopDetector()
    elif kind == "ultralytics":
        detector = UltralyticsDetector(weights)
    elif kind == "remote":
        endpoints = [url.strip().rstrip("/") for url in remote_inference_url.split(",") if url.strip()]
        detector_type = FailoverRemoteWorkerDetector if len(endpoints) > 1 else RemoteWorkerDetector
        detector = detector_type(
            endpoints if detector_type is FailoverRemoteWorkerDetector else endpoints[0],
            remote_worker_secret,
            remote_inference_timeout_ms,
            tls_ca_file=remote_tls_ca_file,
            tls_client_cert_file=remote_tls_client_cert_file,
            tls_client_key_file=remote_tls_client_key_file,
        )
    else:
        raise RuntimeError("unknown DETECTOR=%s" % kind)
    if cloud_provider is not None:
        from .cloud_fallback import CloudFallbackDetector
        return CloudFallbackDetector(detector, cloud_provider, cloud_min_confidence)
    return detector
