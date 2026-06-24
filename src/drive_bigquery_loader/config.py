from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class TargetFileConfig:
    file_name: str
    table_name: str
    duplicate_policy: str
    duplicate_key_columns: list[str]
    expected_header: list[str]
    bq_columns: list[str]

    def source_column_for_bq(self, bq_column: str) -> str:
        try:
            index = self.bq_columns.index(bq_column)
        except ValueError as exc:
            raise ValueError(
                f"{self.file_name}: BigQuery column is not configured: {bq_column}"
            ) from exc
        return self.expected_header[index]

    @property
    def key_columns(self) -> list[str]:
        """Backward-compatible alias for business duplicate key columns."""
        return self.duplicate_key_columns


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]
    target_files: list[TargetFileConfig]

    @property
    def drive_folder_id(self) -> str:
        return self.raw["drive"]["folder_id"]

    @property
    def bq_project_id(self) -> str:
        return self.raw["bigquery"]["project_id"]

    @property
    def bq_dataset_id(self) -> str:
        return self.raw["bigquery"]["dataset_id"]

    @property
    def bq_location(self) -> str:
        return self.raw["bigquery"]["location"]

    @property
    def archive_bucket(self) -> str:
        return self.raw["gcs"]["archive_bucket"]

    @property
    def archive_prefix(self) -> str:
        return self.raw["gcs"]["archive_prefix"].strip("/")


def load_config(path: str | Path | list[str | Path]) -> AppConfig:
    config_paths = (
        [Path(item) for item in path]
        if isinstance(path, list)
        else [Path(path)]
    )
    raw: dict[str, Any] = {}
    for config_path in config_paths:
        with config_path.open("r", encoding="utf-8") as fp:
            loaded = yaml.safe_load(fp) or {}
        raw = _deep_merge(raw, loaded)

    target_files: list[TargetFileConfig] = []
    for item in raw["drive"]["target_files"]:
        expected_header = list(item["expected_header"])
        bq_columns = list(item.get("bq_columns", expected_header))
        if len(expected_header) != len(bq_columns):
            raise ValueError(
                f"{item['file_name']}: expected_header and bq_columns length mismatch"
            )
        target_files.append(
            TargetFileConfig(
                file_name=item["file_name"],
                table_name=item["table_name"],
                duplicate_policy=item["duplicate_policy"],
                duplicate_key_columns=list(
                    item.get("duplicate_key_columns", item.get("key_columns", []))
                ),
                expected_header=expected_header,
                bq_columns=bq_columns,
            )
        )
    return AppConfig(raw=raw, target_files=target_files)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
