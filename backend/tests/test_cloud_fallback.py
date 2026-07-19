import io

from PIL import Image

from akshrava_backend.cloud_fallback import CloudFallbackDetector, CloudImageProvider, CloudObject, CloudResult
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
