import pytest

from akshrava_backend.detector import UltralyticsDetector


def test_yolo_detector_refuses_missing_weights_before_import_or_download():
    with pytest.raises(RuntimeError, match="local.*model file"):
        UltralyticsDetector("/definitely/not/a/model.pt")
