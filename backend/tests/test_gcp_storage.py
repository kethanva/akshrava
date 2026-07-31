from unittest.mock import MagicMock, patch
import pytest

from akshrava_backend.gcp_storage import GcpDiagnosticStorage


@pytest.mark.asyncio
async def test_upload_frame_succeeds_with_mock_client():
    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()

    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    mock_storage_module = MagicMock()
    mock_storage_module.Client.return_value = mock_client

    mock_google_cloud = MagicMock()
    mock_google_cloud.storage = mock_storage_module
    with patch.dict("sys.modules", {"google.cloud": mock_google_cloud, "google.cloud.storage": mock_storage_module}):
        storage_service = GcpDiagnosticStorage(bucket_name="test-bucket")

        # Double call init client to test lock double-check return path
        storage_service._init_client()
        storage_service._init_client()

        # Test file upload
        url = await storage_service.upload_frame("frame_1.jpg", b"fake-jpeg-data")

        # Verify blob functions were called with correct parameters
        mock_client.bucket.assert_called_once_with("test-bucket")
        mock_bucket.blob.assert_called_once_with("frame_1.jpg")
        mock_blob.upload_from_string.assert_called_once_with(
            b"fake-jpeg-data", content_type="image/jpeg", timeout=30
        )
        assert url == "gs://test-bucket/frame_1.jpg"

        # Test close
        storage_service.close()


def test_gcp_storage_requires_bucket_name():
    storage_service = GcpDiagnosticStorage(bucket_name="")
    with patch.dict("os.environ", {"GCP_DIAGNOSTICS_BUCKET": ""}):
        storage_service.bucket_name = ""
        with pytest.raises(ValueError, match="GCP_DIAGNOSTICS_BUCKET must be set"):
            storage_service._init_client()


def test_gcp_storage_missing_google_cloud_storage_module():
    storage_service = GcpDiagnosticStorage(bucket_name="my-bucket")
    with patch.dict("sys.modules", {"google.cloud.storage": None}):
        with pytest.raises(ImportError, match="gcp dependency group not installed"):
            storage_service._init_client()


@pytest.mark.asyncio
async def test_upload_frame_validation_errors():
    storage_service = GcpDiagnosticStorage(bucket_name="test-bucket")

    with pytest.raises(ValueError, match="Object name cannot be empty"):
        await storage_service.upload_frame("", b"bytes")

    with pytest.raises(ValueError, match="path traversal or absolute paths"):
        await storage_service.upload_frame("../traversal.jpg", b"bytes")

    with pytest.raises(ValueError, match="path traversal or absolute paths"):
        await storage_service.upload_frame("/absolute/path.jpg", b"bytes")


def test_gcp_storage_close_when_uninitialized():
    storage_service = GcpDiagnosticStorage(bucket_name="test-bucket")
    storage_service.close()  # Executor is None, should not raise
