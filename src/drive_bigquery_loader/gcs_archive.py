from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from google.cloud import storage

from drive_bigquery_loader.config import AppConfig
from drive_bigquery_loader.models import LocalCsvFile


class GcsArchive:
    def __init__(self, config: AppConfig, client: storage.Client | None = None) -> None:
        self._config = config
        self._client = client or storage.Client(project=config.bq_project_id)

    def upload_raw_csv(self, local_file: LocalCsvFile, batch_id: str) -> str:
        if self._config.archive_bucket.startswith("TODO-"):
            raise ValueError("gcs.archive_bucket must be configured before upload")

        bucket = self._client.bucket(self._config.archive_bucket)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        object_name = (
            f"{self._config.archive_prefix}/batch_id={batch_id}/"
            f"{timestamp}_{local_file.path.name}"
        )
        blob = bucket.blob(object_name)
        blob.upload_from_filename(str(local_file.path), content_type="text/csv")
        return f"gs://{self._config.archive_bucket}/{object_name}"

    def local_stub_uri(self, path: str | Path) -> str:
        return f"file://{Path(path).resolve()}"
