import pytest

from akshrava_backend.detector import UltralyticsDetector
from akshrava_backend.model_integrity import verify_model_sha256


def test_yolo_detector_refuses_missing_weights_before_import_or_download():
    with pytest.raises(RuntimeError, match="local.*model file"):
        UltralyticsDetector("/definitely/not/a/model.pt")


def test_model_sha256_gate_rejects_missing_or_mismatched_hash(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"approved model fixture\n")

    with pytest.raises(RuntimeError, match="required"):
        verify_model_sha256(str(model), "", required=True)
    with pytest.raises(RuntimeError, match="does not match"):
        verify_model_sha256(str(model), "0" * 64, required=True)


def test_model_sha256_gate_accepts_matching_hash(tmp_path):
    model = tmp_path / "model.pt"
    model.write_bytes(b"approved model fixture\n")
    expected = "355a37c17b79eaa4c8b50c1bfd988eabf0ca077d22598232b4afd9d85235c7ba"

    assert verify_model_sha256(str(model), expected, required=True) == expected
