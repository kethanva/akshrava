import os

class GcpDiagnosticStorage:
    """Uploads opted-in diagnostic camera frames to Google Cloud Storage.

    Complies with the 30-day auto-deletion rules via the bucket's lifecycle policy.
    """

    def __init__(self, bucket_name: str = ""):
        self.bucket_name = bucket_name or os.getenv("GCP_DIAGNOSTICS_BUCKET", "")
        self.client = None
        self.bucket = None
        self._executor = None

    def _init_client(self):
        if self.client is None:
            if not self.bucket_name:
                raise ValueError("GCP_DIAGNOSTICS_BUCKET must be set to use GCP Storage.")
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise ImportError("gcp dependency group not installed; run pip install '.[gcp]'") from exc
            self.client = storage.Client()
            self.bucket = self.client.bucket(self.bucket_name)
            from concurrent.futures import ThreadPoolExecutor
            self._executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="akshrava-gcs-upload")

    async def upload_frame(self, file_name: str, jpeg_bytes: bytes) -> str:
        """Uploads a raw JPEG image to the GCS bucket.

        Returns the public URL (or GS URI if authenticated-only access is preferred).
        """
        self._init_client()
        # GCS uploads are blocking, so run in the isolated executor to avoid event loop lag
        import asyncio
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._upload_blocking, file_name, jpeg_bytes)
        return f"gs://{self.bucket_name}/{file_name}"

    def _upload_blocking(self, file_name: str, jpeg_bytes: bytes):
        blob = self.bucket.blob(file_name)
        blob.upload_from_string(jpeg_bytes, content_type="image/jpeg")

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=False)

