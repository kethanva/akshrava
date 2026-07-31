import io

from PIL import Image

from akshrava_backend.cloud_fallback import (
    CloudFallbackDetector,
    CloudImageProvider,
    CloudObject,
    CloudResult,
)
from akshrava_backend.detector import Detector
from akshrava_backend.domain import Detection


class EmptyLocalDetector(Detector):
    def detect(self, jpeg):
        return []


class LocalDetector(Detector):
    def detect(self, jpeg):
        return [Detection("person", 0.9, (1, 1, 2, 2))]


class ParallelLocalDetector(EmptyLocalDetector):
    def requires_serial_execution(self):
        return False


class StubCloudProvider(CloudImageProvider):
    name = "stub"

    def __init__(self):
        self.called = False

    def analyze(self, jpeg):
        self.called = True
        return CloudResult(self.name, [CloudObject("Car", 0.8, (0.25, 0.2, 0.75, 0.9), True)])


def jpeg(width=4, height=2):
    output = io.BytesIO()
    Image.new("RGB", (width, height)).save(output, format="JPEG")
    return output.getvalue()


def test_cloud_fallback_only_runs_after_empty_local_result_and_scales_normalized_boxes():
    provider = StubCloudProvider()
    detector = CloudFallbackDetector(EmptyLocalDetector(), provider, 0.55)
    detections, unavailable = detector.detect_with_status(jpeg())
    assert provider.called
    assert detections == [Detection("car", 0.8, (1.0, 0.4, 3.0, 1.8))]
    assert not unavailable


def test_cloud_fallback_does_not_send_images_when_local_detector_found_an_object():
    provider = StubCloudProvider()
    detector = CloudFallbackDetector(LocalDetector(), provider, 0.55)
    assert detector.detect_with_status(b"unused")[0][0].label == "person"
    assert not provider.called


def test_cloud_provider_failure_exposes_only_a_coarse_availability_signal():
    class FailingCloudProvider(CloudImageProvider):
        name = "stub"

        def analyze(self, jpeg):
            raise RuntimeError("provider unavailable")

    detector = CloudFallbackDetector(EmptyLocalDetector(), FailingCloudProvider(), 0.55)
    assert detector.detect_with_status(jpeg()) == ([], True)


def test_cloud_fallback_status_is_returned_with_its_own_frame():
    class AlternatingProvider(CloudImageProvider):
        name = "alternating"

        def __init__(self):
            self.calls = 0

        def analyze(self, jpeg):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary failure")
            return CloudResult(self.name, [])

    detector = CloudFallbackDetector(EmptyLocalDetector(), AlternatingProvider(), 0.55)
    assert detector.detect_with_status(jpeg()) == ([], True)
    assert detector.detect_with_status(jpeg()) == ([], False)


def test_cloud_wrapper_preserves_a_stateless_remote_detector_parallel_contract():
    detector = CloudFallbackDetector(ParallelLocalDetector(), StubCloudProvider(), 0.55)
    assert detector.requires_serial_execution() is False


import pytest
from unittest.mock import MagicMock, patch
from akshrava_backend.cloud_fallback import (
    CloudProviderError,
    AwsRekognitionProvider,
    GcpVisionProvider,
    AzureImageAnalysisProvider,
    make_cloud_provider,
)


def test_base_cloud_image_provider_raises_not_implemented():
    provider = CloudImageProvider()
    with pytest.raises(NotImplementedError):
        provider.analyze(b"test")


def test_cloud_fallback_detect_and_detect_for_device_wrappers():
    provider = StubCloudProvider()
    detector = CloudFallbackDetector(EmptyLocalDetector(), provider, 0.55)
    assert len(detector.detect(jpeg())) == 1
    assert len(detector.detect_for_device("dev1", jpeg())) == 1


def test_cloud_fallback_handles_corrupt_image_during_scaling():
    class InvalidBoxProvider(CloudImageProvider):
        name = "invalid_box"
        def analyze(self, jpeg):
            return CloudResult("invalid", [CloudObject("person", 0.9, (0.1, 0.1, 0.5, 0.5), normalized=True)])

    detector = CloudFallbackDetector(EmptyLocalDetector(), InvalidBoxProvider(), 0.55)
    # Pass invalid jpeg bytes so PIL fails to open it during scaling
    dets, failed = detector.detect_with_status_for_device("dev1", b"not-a-jpeg")
    assert dets == []
    assert failed is True


@pytest.mark.asyncio
async def test_cloud_fallback_detect_async_methods():
    provider = StubCloudProvider()
    detector = CloudFallbackDetector(EmptyLocalDetector(), provider, 0.55)
    
    # Test hit via detect_async and detect_async_for_device
    dets1 = await detector.detect_async(jpeg())
    assert len(dets1) == 1
    
    dets2 = await detector.detect_async_for_device("dev1", jpeg())
    assert len(dets2) == 1

    # Test local detector hit short-circuit in async
    detector_hit = CloudFallbackDetector(LocalDetector(), provider, 0.55)
    dets_hit, unavailable = await detector_hit.detect_async_with_status_for_device("dev1", jpeg())
    assert len(dets_hit) == 1
    assert unavailable is False

    # Test provider failure in async
    class FailingProvider(CloudImageProvider):
        name = "fail"
        def analyze(self, j):
            raise RuntimeError("fail")

    detector_fail = CloudFallbackDetector(EmptyLocalDetector(), FailingProvider(), 0.55)
    dets_fail, unavailable_fail = await detector_fail.detect_async_with_status_for_device("dev1", jpeg())
    assert dets_fail == []
    assert unavailable_fail is True

    # Test corrupt image parse failure in async
    class CorruptAsyncProvider(CloudImageProvider):
        name = "corrupt"
        def analyze(self, j):
            return CloudResult("corrupt", [CloudObject("car", 0.9, (0.1, 0.1, 0.5, 0.5), normalized=True)])

    detector_corrupt = CloudFallbackDetector(EmptyLocalDetector(), CorruptAsyncProvider(), 0.55)
    dets_c, un_c = await detector_corrupt.detect_async_with_status_for_device("dev1", b"not-jpeg")
    assert dets_c == []
    assert un_c is True


@pytest.mark.asyncio
async def test_cloud_fallback_close_delegates_to_local():
    class SyncCloseDetector(EmptyLocalDetector):
        def __init__(self):
            self.closed = False
        def close(self):
            self.closed = True

    class AsyncCloseDetector(EmptyLocalDetector):
        def __init__(self):
            self.closed = False
        async def close(self):
            self.closed = True

    sync_det = SyncCloseDetector()
    cf1 = CloudFallbackDetector(sync_det, StubCloudProvider(), 0.55)
    await cf1.close()
    assert sync_det.closed

    async_det = AsyncCloseDetector()
    cf2 = CloudFallbackDetector(async_det, StubCloudProvider(), 0.55)
    await cf2.close()
    assert async_det.closed


def test_aws_rekognition_provider_and_factory(mock_aws_rekognition_client):
    mock_boto3 = MagicMock()
    mock_boto3.client.return_value = mock_aws_rekognition_client
    with patch.dict("sys.modules", {"boto3": mock_boto3, "botocore.config": MagicMock()}):
        provider = AwsRekognitionProvider(region="us-east-1")
        assert provider.name == "aws"
        res = provider.analyze(jpeg())
        assert len(res.labels) == 2
        assert res.labels[0].label == "Person"

    # Test import error when boto3 missing
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(CloudProviderError):
            AwsRekognitionProvider(region="us-east-1")


def test_gcp_vision_provider_and_factory(mock_gcp_vision_client):
    mock_vision_module = MagicMock()
    mock_vision_module.ImageAnnotatorClient.return_value = mock_gcp_vision_client
    # `from google.cloud import vision` resolves the submodule as an attribute of the parent
    # package, so the parent stub must carry it: a bare MagicMock() parent hands back an
    # auto-generated child mock instead and the provider silently analyses nothing.
    mock_google_cloud = MagicMock()
    mock_google_cloud.vision = mock_vision_module
    with patch.dict(
        "sys.modules",
        {"google.cloud": mock_google_cloud, "google.cloud.vision": mock_vision_module},
    ):
        provider = GcpVisionProvider()
        assert provider.name == "gcp"
        res = provider.analyze(jpeg())
        assert len(res.labels) == 2
        assert res.labels[0].label == "Person"

    with patch.dict("sys.modules", {"google.cloud.vision": None}):
        with pytest.raises(CloudProviderError):
            GcpVisionProvider()


def test_azure_image_analysis_provider_and_factory(mock_azure_vision_client):
    mock_azure_module = MagicMock()
    mock_azure_module.ImageAnalysisClient.return_value = mock_azure_vision_client
    mock_azure_module.models.VisualFeatures.OBJECTS = "OBJECTS"

    with patch.dict("sys.modules", {
        "azure.ai.vision.imageanalysis": mock_azure_module,
        "azure.ai.vision.imageanalysis.models": mock_azure_module.models,
        "azure.core.credentials": MagicMock(),
    }):
        provider = AzureImageAnalysisProvider(endpoint="https://example.cognitiveservices.azure.com/", key="secret")
        assert provider.name == "azure"
        res = provider.analyze(jpeg())
        assert len(res.labels) == 1

        # Test no tags branch in azure
        obj_no_tags = MagicMock()
        obj_no_tags.tags = None
        mock_azure_vision_client.analyze.return_value.objects.list = [obj_no_tags]
        res_no_tags = provider.analyze(jpeg())
        assert len(res_no_tags.labels) == 0

    with patch.dict("sys.modules", {"azure.ai.vision.imageanalysis": None}):
        with pytest.raises(CloudProviderError):
            AzureImageAnalysisProvider("ep", "key")


def test_make_cloud_provider():
    assert make_cloud_provider("none", "us-east-1", "", "") is None

    with patch("akshrava_backend.cloud_fallback.AwsRekognitionProvider") as mock_aws:
        make_cloud_provider("aws", "us-west-2", "", "")
        mock_aws.assert_called_once_with("us-west-2")

    with patch("akshrava_backend.cloud_fallback.GcpVisionProvider") as mock_gcp:
        make_cloud_provider("gcp", "", "", "")
        mock_gcp.assert_called_once()

    with patch("akshrava_backend.cloud_fallback.AzureImageAnalysisProvider") as mock_azure:
        make_cloud_provider("azure", "", "https://azure.endpoint", "key123")
        mock_azure.assert_called_once_with("https://azure.endpoint", "key123")

    with pytest.raises(CloudProviderError, match="AZURE_VISION_ENDPOINT and AZURE_VISION_KEY are required"):
        make_cloud_provider("azure", "", "", "")

    with pytest.raises(CloudProviderError, match="unknown CLOUD_FALLBACK_PROVIDER"):
        make_cloud_provider("invalid_kind", "", "", "")

