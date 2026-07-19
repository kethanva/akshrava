"""Optional cloud image enrichment used only after local detection returns no objects.

Only a small allow-list of *boxed* labels can re-enter the conservative hazard pipeline.  Free
form captions and broad image tags never leave this module: returning them to the handset would
needlessly disclose cloud-derived scene details without an approved operator-consent workflow.
"""

from dataclasses import dataclass
import io
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from PIL import Image

from .domain import Detection
from .detector import Detector

logger = logging.getLogger(__name__)


class CloudProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class CloudObject:
    label: str
    confidence: float
    box: Optional[Tuple[float, float, float, float]] = None
    normalized: bool = False


@dataclass(frozen=True)
class CloudResult:
    provider: str
    labels: List[CloudObject]


class CloudImageProvider:
    name = "unknown"

    def analyze(self, jpeg: bytes) -> CloudResult:
        raise NotImplementedError


_SAFE_LABELS: Dict[str, str] = {
    "person": "person",
    "bicycle": "bicycle",
    "bike": "bicycle",
    "motorcycle": "motorcycle",
    "motorbike": "motorcycle",
    "car": "car",
    "truck": "truck",
    "bus": "bus",
    "dog": "dog",
    "cat": "cat",
}


class CloudFallbackDetector(Detector):
    """Preserves local-first behavior without returning cloud-derived scene metadata."""

    _LOG_INTERVAL_S = 60.0

    def __init__(self, local: Detector, provider: CloudImageProvider, min_confidence: float):
        self.local = local
        self.provider = provider
        self.min_confidence = min_confidence
        self._last_logged_failure_at = 0.0
        self._log_lock = threading.Lock()

    def detect(self, jpeg: bytes) -> List[Detection]:
        return self.detect_with_status(jpeg)[0]

    def detect_for_device(self, device_id: str, jpeg: bytes) -> List[Detection]:
        return self.detect_with_status_for_device(device_id, jpeg)[0]

    def detect_with_status(self, jpeg: bytes) -> Tuple[List[Detection], bool]:
        return self.detect_with_status_for_device("", jpeg)

    def detect_with_status_for_device(self, device_id: str, jpeg: bytes) -> Tuple[List[Detection], bool]:
        """Return frame-local availability with detections; never share it between devices."""
        local_detections = self.local.detect_for_device(device_id, jpeg)
        if local_detections:
            return local_detections, False
        try:
            result = self.provider.analyze(jpeg)
        except Exception:
            # A vendor outage must remain indistinguishable from no cloud enrichment to the
            # hazard scorer, but the phone receives a coarse availability bit so it does not
            # silently imply that the configured fallback is still helping. A dead vendor must
            # also not be silent to the operator: log it, rate-limited so a sustained outage
            # does not spam the log once per frame.
            with self._log_lock:
                now = time.monotonic()
                if now - self._last_logged_failure_at >= self._LOG_INTERVAL_S:
                    self._last_logged_failure_at = now
                    logger.warning("cloud fallback provider %s failed", self.provider.name, exc_info=True)
            return [], True
        detections = []
        width = height = 0
        for item in result.labels:
            label = _SAFE_LABELS.get(item.label.lower())
            if label and item.box is not None and item.confidence >= self.min_confidence:
                box = item.box
                if item.normalized:
                    if not width:
                        with Image.open(io.BytesIO(jpeg)) as image:
                            width, height = image.size
                    box = (box[0] * width, box[1] * height, box[2] * width, box[3] * height)
                detections.append(Detection(label=label, confidence=item.confidence, box=box))
        return detections, False

    def requires_serial_execution(self) -> bool:
        # Frame-local status is returned by detect_with_status(), so independent sessions no
        # longer share a mutable outcome flag and may use remote inference concurrently.
        return self.local.requires_serial_execution()

class AwsRekognitionProvider(CloudImageProvider):
    name = "aws"

    def __init__(self, region: str):
        try:
            import boto3
        except ImportError as exc:
            raise CloudProviderError("install backend[aws] for AWS cloud fallback") from exc
        self.client = boto3.client("rekognition", region_name=region)

    def analyze(self, jpeg: bytes) -> CloudResult:
        response = self.client.detect_labels(
            Image={"Bytes": jpeg}, Features=["GENERAL_LABELS"], MaxLabels=10, MinConfidence=55
        )
        labels = []
        for label in response.get("Labels", []):
            for instance in label.get("Instances", []):
                box = instance.get("BoundingBox", {})
                labels.append(CloudObject(
                    label=label["Name"], confidence=float(instance.get("Confidence", 0)) / 100,
                box=(box.get("Left", 0.0), box.get("Top", 0.0),
                         box.get("Left", 0.0) + box.get("Width", 0.0),
                         box.get("Top", 0.0) + box.get("Height", 0.0)), normalized=True,
                ))
        return CloudResult(self.name, labels)


class GcpVisionProvider(CloudImageProvider):
    name = "gcp"

    def __init__(self):
        try:
            from google.cloud import vision
        except ImportError as exc:
            raise CloudProviderError("install backend[gcp] for GCP cloud fallback") from exc
        self.vision = vision
        self.client = vision.ImageAnnotatorClient()

    def analyze(self, jpeg: bytes) -> CloudResult:
        image = self.vision.Image(content=jpeg)
        objects = self.client.object_localization(image=image).localized_object_annotations
        parsed = []
        for item in objects:
            vertices = item.bounding_poly.normalized_vertices
            xs = [vertex.x for vertex in vertices]
            ys = [vertex.y for vertex in vertices]
            if xs and ys:
                parsed.append(CloudObject(
                    item.name, float(item.score), (min(xs), min(ys), max(xs), max(ys)), normalized=True
                ))
        return CloudResult(self.name, parsed)


class AzureImageAnalysisProvider(CloudImageProvider):
    name = "azure"

    def __init__(self, endpoint: str, key: str):
        try:
            from azure.ai.vision.imageanalysis import ImageAnalysisClient
            from azure.ai.vision.imageanalysis.models import VisualFeatures
            from azure.core.credentials import AzureKeyCredential
        except ImportError as exc:
            raise CloudProviderError("install backend[azure] for Azure cloud fallback") from exc
        self.features = VisualFeatures
        self.client = ImageAnalysisClient(endpoint=endpoint, credential=AzureKeyCredential(key))

    def analyze(self, jpeg: bytes) -> CloudResult:
        result = self.client.analyze(
            image_data=jpeg,
            visual_features=[self.features.OBJECTS],
        )
        parsed = []
        for item in result.objects.list if result.objects else []:
            box = item.bounding_box
            tags = getattr(item, "tags", None) or []
            if not tags:
                logger.warning("azure object result had no tag metadata; ignoring it")
                continue
            parsed.append(CloudObject(tags[0].name, float(tags[0].confidence),
                                      (box.x, box.y, box.x + box.w, box.y + box.h)))
        return CloudResult(self.name, parsed)


def make_cloud_provider(kind: str, aws_region: str, azure_endpoint: str, azure_key: str) -> Optional[CloudImageProvider]:
    if kind == "none":
        return None
    if kind == "aws":
        return AwsRekognitionProvider(aws_region)
    if kind == "gcp":
        return GcpVisionProvider()
    if kind == "azure":
        if not azure_endpoint or not azure_key:
            raise CloudProviderError("AZURE_VISION_ENDPOINT and AZURE_VISION_KEY are required")
        return AzureImageAnalysisProvider(azure_endpoint, azure_key)
    raise CloudProviderError("unknown CLOUD_FALLBACK_PROVIDER=%s" % kind)
