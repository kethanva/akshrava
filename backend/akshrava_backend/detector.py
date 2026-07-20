import io
import hashlib
import hmac
import json
import secrets
import ssl
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler, HTTPHandler

from PIL import Image

from .domain import Detection
from .model_integrity import verify_model_sha256


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

    def detect_for_device(self, device_id: str, jpeg: bytes) -> List[Detection]:
        """Detect for an authenticated device.

        Most detectors are device-agnostic. Remote adapters may use the stable device id to keep
        a walking session sticky to one warm inference endpoint while still failing through peers.
        """
        return self.detect(jpeg)

    async def detect_async(self, jpeg: bytes) -> List[Detection]:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.detect, jpeg)

    async def detect_async_for_device(self, device_id: str, jpeg: bytes) -> List[Detection]:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.detect_for_device, device_id, jpeg)

    async def detect_async_with_status_for_device(self, device_id: str, jpeg: bytes) -> Tuple[List[Detection], bool]:
        """Return detections along with fallback status. Defaults to False (fallback not active)."""
        return await self.detect_async_for_device(device_id, jpeg), False


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


class WorkerSaturatedError(RemoteInferenceError):
    """Worker queue is full (HTTP 503). Soft-shed the frame; do not tear down the WebSocket."""


class _RejectRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RemoteInferenceError("remote worker redirect rejected")


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
        allowed_hosts: Optional[Set[str]] = None,
    ):
        self.endpoint = endpoint
        parsed = urlparse(endpoint)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError("remote worker endpoint must include scheme and host")
        self._endpoint_host = parsed.hostname.lower()
        self._allowed_hosts = {host.lower() for host in (allowed_hosts or {self._endpoint_host})}
        if self._endpoint_host not in self._allowed_hosts:
            raise ValueError("remote worker endpoint host is not on the allowlist")
        self.shared_secret = shared_secret.encode("utf-8")
        self.timeout_seconds = timeout_ms / 1000.0
        self._ssl_context = None
        if tls_ca_file:
            self._ssl_context = ssl.create_default_context(cafile=tls_ca_file)
            self._ssl_context.load_cert_chain(tls_client_cert_file, tls_client_key_file)
        self._async_client = None

    def _assert_host_allowed(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        if host not in self._allowed_hosts:
            raise RemoteInferenceError("remote worker host not allowlisted")

    async def _get_async_client(self):
        if self._async_client is None:
            import httpx
            limits = httpx.Limits(max_keepalive_connections=100, max_connections=200)
            self._async_client = httpx.AsyncClient(
                verify=self._ssl_context or True,
                timeout=self.timeout_seconds,
                limits=limits,
                follow_redirects=False,
            )
        return self._async_client

    def detect(self, jpeg: bytes) -> List[Detection]:
        self._assert_host_allowed(self.endpoint)
        body = jpeg
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        signature = hmac.new(
            self.shared_secret,
            timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Content-Type": "image/jpeg",
            "X-Akshrava-Timestamp": timestamp,
            "X-Akshrava-Nonce": nonce,
            "X-Akshrava-Signature": signature,
        }
        from .tracing import inject_trace_headers, start_inference_span

        with start_inference_span("remote.detect"):
            inject_trace_headers(headers)
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers=headers,
        )
        handlers = [_RejectRedirectHandler()]
        if self._ssl_context is not None:
            handlers.append(HTTPSHandler(context=self._ssl_context))
        else:
            handlers.extend([HTTPHandler(), HTTPSHandler()])
        opener = build_opener(*handlers)
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self._MAX_RESPONSE_BYTES + 1)
        except RemoteInferenceError:
            raise
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

    async def detect_async(self, jpeg: bytes) -> List[Detection]:
        self._assert_host_allowed(self.endpoint)
        client = await self._get_async_client()
        body = jpeg
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        signature = hmac.new(
            self.shared_secret,
            timestamp.encode("ascii") + b"." + nonce.encode("ascii") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Content-Type": "image/jpeg",
            "X-Akshrava-Timestamp": timestamp,
            "X-Akshrava-Nonce": nonce,
            "X-Akshrava-Signature": signature,
        }
        from .tracing import inject_trace_headers, start_inference_span

        with start_inference_span("remote.detect_async"):
            inject_trace_headers(headers)
            try:
                response = await client.post(
                    self.endpoint,
                    content=body,
                    headers=headers,
                    follow_redirects=False,
                )
                if response.status_code == 503:
                    raise WorkerSaturatedError("worker inference queue full")
                response.raise_for_status()
                raw = response.content
            except WorkerSaturatedError:
                raise
            except Exception as exc:
                import httpx
                if isinstance(exc, httpx.HTTPStatusError):
                    if 300 <= exc.response.status_code < 400:
                        raise RemoteInferenceError("remote worker redirect rejected") from exc
                    if exc.response.status_code == 503:
                        raise WorkerSaturatedError("worker inference queue full") from exc
                    raise RemoteInferenceError(
                        "remote worker failed status=%d" % exc.response.status_code
                    ) from exc
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

    async def close(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

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


@dataclass(frozen=True)
class InferenceEndpoint:
    id: str
    url: str
    enabled: bool = True


class StaticInferenceEndpointRegistry:
    """Static endpoint registry used until a dynamic fleet control plane exists.

    The registry gives the control plane explicit endpoint identities and stable device placement.
    It is still configured at deploy time, but it is no longer a blind comma-separated retry list.
    """

    def __init__(self, endpoints: List[InferenceEndpoint]):
        enabled = [endpoint for endpoint in endpoints if endpoint.enabled]
        if not enabled:
            raise ValueError("at least one enabled inference endpoint is required")
        self._endpoints = enabled

    @classmethod
    def from_urls(cls, urls: List[str]):
        endpoints = [
            InferenceEndpoint(id="worker-%d" % (index + 1), url=url)
            for index, url in enumerate(urls)
        ]
        return cls(endpoints)

    @classmethod
    def from_json(cls, raw: str):
        try:
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("REMOTE_INFERENCE_REGISTRY_JSON must be valid JSON") from exc
        if not isinstance(items, list):
            raise ValueError("REMOTE_INFERENCE_REGISTRY_JSON must be a list")
        endpoints = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError("REMOTE_INFERENCE_REGISTRY_JSON entries must be objects")
            url = str(item.get("url", "")).strip().rstrip("/")
            endpoint_id = str(item.get("id") or "worker-%d" % (index + 1)).strip()
            enabled = bool(item.get("enabled", True))
            if not url or not endpoint_id:
                raise ValueError("REMOTE_INFERENCE_REGISTRY_JSON entries require id and url")
            endpoints.append(InferenceEndpoint(endpoint_id, url, enabled))
        return cls(endpoints)

    @property
    def endpoints(self) -> List[InferenceEndpoint]:
        return list(self._endpoints)

    def ordered_for_device(self, device_id: str) -> List[InferenceEndpoint]:
        digest = hashlib.sha256(device_id.encode("utf-8")).digest()
        start = int.from_bytes(digest[:8], "big") % len(self._endpoints)
        return self._endpoints[start:] + self._endpoints[:start]

    def allowed_hosts(self) -> Set[str]:
        hosts = set()
        for endpoint in self._endpoints:
            host = urlparse(endpoint.url).hostname
            if host:
                hosts.add(host.lower())
        return hosts


class RegistryRemoteWorkerDetector(Detector):
    """Device-sticky worker selection with immediate fail-through to warm peers."""

    def __init__(self, registry: StaticInferenceEndpointRegistry, shared_secret: str, timeout_ms: int, **tls):
        self.registry = registry
        allowed = registry.allowed_hosts()
        self._workers = {
            endpoint.id: RemoteWorkerDetector(
                endpoint.url, shared_secret, timeout_ms, allowed_hosts=allowed, **tls
            )
            for endpoint in registry.endpoints
        }

    def detect(self, jpeg: bytes) -> List[Detection]:
        return self.detect_for_device("", jpeg)

    def detect_for_device(self, device_id: str, jpeg: bytes) -> List[Detection]:
        errors = []
        for endpoint in self.registry.ordered_for_device(device_id):
            try:
                return self._workers[endpoint.id].detect(jpeg)
            except RemoteInferenceError as exc:
                errors.append(exc)
        aggregated = self._aggregate_remote_errors(errors)
        raise aggregated from errors[-1]

    async def detect_async(self, jpeg: bytes) -> List[Detection]:
        return await self.detect_async_for_device("", jpeg)

    async def detect_async_for_device(self, device_id: str, jpeg: bytes) -> List[Detection]:
        errors = []
        for endpoint in self.registry.ordered_for_device(device_id):
            try:
                return await self._workers[endpoint.id].detect_async(jpeg)
            except RemoteInferenceError as exc:
                errors.append(exc)
        aggregated = self._aggregate_remote_errors(errors)
        raise aggregated from errors[-1]

    @staticmethod
    def _aggregate_remote_errors(errors: List[Exception]) -> RemoteInferenceError:
        """Preserve WorkerSaturatedError so the API can soft-shed without tearing down WSS.

        A single-endpoint pilot that returns HTTP 503 must not be rewritten into a generic
        RemoteInferenceError — that path closes the socket as vision_unavailable.
        """
        if not errors:
            return RemoteInferenceError("all configured remote workers are unavailable")
        if all(isinstance(exc, WorkerSaturatedError) for exc in errors):
            return WorkerSaturatedError("all configured remote workers are saturated")
        return RemoteInferenceError("all configured remote workers are unavailable")

    def requires_serial_execution(self) -> bool:
        return False

    async def close(self) -> None:
        import asyncio
        await asyncio.gather(*(worker.close() for worker in self._workers.values()))


class UltralyticsDetector(Detector):
    """Optional detector. Enable only after licence/weights review and benchmarking."""

    def __init__(self, weights: str, expected_sha256: str = "", require_sha256: bool = False):
        verify_model_sha256(weights, expected_sha256, required=require_sha256)
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("install backend[yolo] to enable DETECTOR=ultralytics") from exc
        self._model = YOLO(weights)
        from concurrent.futures import ThreadPoolExecutor
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="akshrava-local-infer")

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

    async def detect_async(self, jpeg: bytes) -> List[Detection]:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.detect, jpeg)

    async def detect_async_for_device(self, device_id: str, jpeg: bytes) -> List[Detection]:
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.detect_for_device, device_id, jpeg)

    def close(self) -> None:
        self._executor.shutdown(wait=True)


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
    remote_inference_registry_json: str = "",
    yolo_weights_sha256: str = "",
    require_yolo_sha256: bool = False,
) -> Detector:
    if kind == "noop":
        detector = NoopDetector()
    elif kind == "ultralytics":
        detector = UltralyticsDetector(weights, yolo_weights_sha256, require_yolo_sha256)
    elif kind == "remote":
        endpoints = [url.strip().rstrip("/") for url in remote_inference_url.split(",") if url.strip()]
        registry = (
            StaticInferenceEndpointRegistry.from_json(remote_inference_registry_json)
            if remote_inference_registry_json
            else StaticInferenceEndpointRegistry.from_urls(endpoints)
        )
        detector = RegistryRemoteWorkerDetector(
            registry,
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
