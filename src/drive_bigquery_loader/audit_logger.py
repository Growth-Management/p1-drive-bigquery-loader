from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery

from drive_bigquery_loader.config import AppConfig


class AuditLogger:
    def __init__(self, config: AppConfig, client: bigquery.Client | None = None) -> None:
        self._config = config
        self._client = client
        self._logger = logging.getLogger("drive_bigquery_loader.audit")

    def event(
        self,
        batch_id: str,
        event_type: str,
        payload: dict[str, Any],
        severity: str = "INFO",
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "environment": self._config.raw.get("app", {}).get("environment"),
            "batch_id": batch_id,
            "event_type": event_type,
            "payload": self._jsonable(payload),
        }
        self._logger.info(json.dumps(record, ensure_ascii=False))

        if self._config.raw["audit"]["enable_bigquery_log"]:
            self._insert_bigquery_record(record)

    def _insert_bigquery_record(self, record: dict[str, Any]) -> None:
        if self._client is None:
            self._client = bigquery.Client(
                project=self._config.bq_project_id,
                location=self._config.bq_location,
            )
        errors = self._client.insert_rows_json(
            self._config.raw["audit"]["log_table"],
            [record],
        )
        if errors:
            raise RuntimeError(f"Failed to insert audit log: {errors}")

    def _jsonable(self, value: Any) -> Any:
        if is_dataclass(value):
            return self._jsonable(asdict(value))
        if isinstance(value, dict):
            return {key: self._jsonable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._jsonable(item) for item in value]
        if isinstance(value, set):
            return sorted(self._jsonable(item) for item in value)
        if isinstance(value, Path):
            return str(value)
        return value
