"""Object-name validation runs before any GCS client init, so these need no gcp SDK installed."""

import pytest

from akshrava_backend.gcp_storage import GcpDiagnosticStorage


@pytest.mark.asyncio
async def test_upload_frame_rejects_path_traversal_and_absolute_names():
    service = GcpDiagnosticStorage(bucket_name="test-bucket")
    with pytest.raises(ValueError, match="path traversal"):
        await service.upload_frame("../etc/passwd", b"x")
    with pytest.raises(ValueError, match="path traversal"):
        await service.upload_frame("/absolute/name.jpg", b"x")
    with pytest.raises(ValueError, match="empty"):
        await service.upload_frame("", b"x")
