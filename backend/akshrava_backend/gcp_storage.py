import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# Diagnostic uploads are a consented, blur-gated workflow (see config validation and Important Architecture.md, privacy).
# This class is the transport only; it never decides consent. Bound the pool so a burst of
# uploads cannot spawn dozens of threads each pinning a full JPEG in memory.
_MAX_UPLOAD_WORKERS = 8


class GcpDiagnosticStorage:
    """Uploads consented, blur-gated diagnostic camera frames to Google Cloud Storage.

    The bucket is authenticated-access only; this returns a ``gs://`` URI, never a public URL.
    Objects are auto-deleted by the bucket's 30-day lifecycle policy.
    """

    def __init__(self, bucket_name: str = ""):
        self.bucket_name = bucket_name or os.getenv("GCP_DIAGNOSTICS_BUCKET", "")
        self.client = None
        self.bucket = None
        self._executor = None
        self._init_lock = threading.Lock()

    def _init_client(self):
        if self.client is not None:
            return
        with self._init_lock:
            if self.client is not None:
                return
            if not self.bucket_name:
                raise ValueError("GCP_DIAGNOSTICS_BUCKET must be set to use GCP Storage.")
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise ImportError("gcp dependency group not installed; run pip install '.[gcp]'") from exc
            self.client = storage.Client()
            self.bucket = self.client.bucket(self.bucket_name)
            self._executor = ThreadPoolExecutor(
                max_workers=_MAX_UPLOAD_WORKERS, thread_name_prefix="akshrava-gcs-upload"
            )

    async def upload_frame(self, file_name: str, jpeg_bytes: bytes) -> str:
        """Upload one JPEG to the diagnostics bucket and return its ``gs://`` URI.

        GCS uploads block, so they run in a bounded executor off the event loop.
        """
        if not file_name:
            raise ValueError("Object name cannot be empty")
        if ".." in file_name or file_name.startswith("/"):
            raise ValueError("Invalid object name: path traversal or absolute paths are not allowed")
        self._init_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._upload_blocking, file_name, jpeg_bytes)
        return f"gs://{self.bucket_name}/{file_name}"

    def _upload_blocking(self, file_name: str, jpeg_bytes: bytes):
        blob = self.bucket.blob(file_name)
        blob.upload_from_string(jpeg_bytes, content_type="image/jpeg", timeout=30)

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=False)
