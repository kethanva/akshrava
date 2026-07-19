import pytest
from unittest.mock import MagicMock, patch

# Skip all tests in this file if google.cloud.storage is not available
pytest.importorskip("google.cloud.storage")

from akshrava_backend.gcp_storage import GcpDiagnosticStorage

@pytest.mark.asyncio
async def test_upload_frame_succeeds_with_mock_client():
    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()

    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    # Patch the google.cloud.storage.Client constructor to return our mock
    with patch("google.cloud.storage.Client", return_value=mock_client):
        storage_service = GcpDiagnosticStorage(bucket_name="test-bucket")
        
        # Test file upload
        url = await storage_service.upload_frame("frame_1.jpg", b"fake-jpeg-data")
        
        # Verify blob functions were called with correct parameters
        mock_client.bucket.assert_called_once_with("test-bucket")
        mock_bucket.blob.assert_called_once_with("frame_1.jpg")
        mock_blob.upload_from_string.assert_called_once_with(
            b"fake-jpeg-data", content_type="image/jpeg"
        )
        assert url == "gs://test-bucket/frame_1.jpg"

def test_gcp_storage_requires_bucket_name():
    storage_service = GcpDiagnosticStorage(bucket_name="")
    with pytest.raises(ValueError, match="GCP_DIAGNOSTICS_BUCKET"):
        storage_service._init_client()
