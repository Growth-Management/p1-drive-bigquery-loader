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
            try:
                self._insert_bigquery_records(record)
            except Exception:
                if self._config.raw["audit"].get("fail_on_bigquery_error", False):
                    raise
                self._logger.exception("Failed to write BigQuery audit log")

    def _insert_bigquery_records(self, record: dict[str, Any]) -> None:
        if self._client is None:
            self._client = bigquery.Client(
                project=self._config.bq_project_id,
                location=self._config.bq_location,
            )
        run_record = self._run_record(record)
        if run_record is not None:
            self._insert_json_rows(
                self._config.raw["audit"]["run_table"],
                [run_record],
            )

        file_records = self._file_records(record)
        if file_records:
            self._insert_json_rows(
                self._config.raw["audit"]["file_table"],
                file_records,
            )

    def _insert_json_rows(self, table_id: str, rows: list[dict[str, Any]]) -> None:
        errors = self._client.insert_rows_json(
            table_id,
            rows,
        )
        if errors:
            raise RuntimeError(f"Failed to insert audit log rows into {table_id}: {errors}")

    def _run_record(self, record: dict[str, Any]) -> dict[str, Any] | None:
        event_type = record["event_type"]
        if event_type not in {
            "batch_started",
            "batch_succeeded",
            "batch_failed",
            "final_replace_skipped",
            "final_replaced",
        }:
            return None

        payload = record.get("payload", {})
        status_by_event = {
            "batch_started": "started",
            "batch_succeeded": "succeeded",
            "batch_failed": "failed",
            "final_replace_skipped": "final_skipped",
            "final_replaced": "final_replaced",
        }
        warnings = payload.get("warnings") if isinstance(payload, dict) else None
        return {
            "timestamp": record["timestamp"],
            "environment": record["environment"],
            "batch_id": record["batch_id"],
            "event_type": event_type,
            "status": status_by_event[event_type],
            "severity": record["severity"],
            "dry_run": payload.get("dry_run") if isinstance(payload, dict) else None,
            "staging_only": payload.get("staging_only") if isinstance(payload, dict) else None,
            "warning_count": len(warnings) if isinstance(warnings, list) else 0,
            "error_message": payload.get("error") if isinstance(payload, dict) else None,
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }

    def _file_records(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        if record["event_type"] != "csv_files_validated":
            return []

        payload = record.get("payload", {})
        results = payload.get("results", {}) if isinstance(payload, dict) else {}
        rows: list[dict[str, Any]] = []
        for file_name, result in results.items():
            if not isinstance(result, dict):
                continue
            errors = result.get("errors", [])
            warnings = result.get("warnings", [])
            header = result.get("header", [])
            rows.append(
                {
                    "timestamp": record["timestamp"],
                    "environment": record["environment"],
                    "batch_id": record["batch_id"],
                    "file_name": file_name,
                    "row_count": result.get("row_count"),
                    "column_count": len(header) if isinstance(header, list) else None,
                    "status": "error" if errors else "ok",
                    "errors_json": json.dumps(errors, ensure_ascii=False),
                    "warnings_json": json.dumps(warnings, ensure_ascii=False),
                }
            )
        return rows

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
