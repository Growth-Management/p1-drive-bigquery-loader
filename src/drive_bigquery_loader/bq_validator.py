from __future__ import annotations

from google.cloud import bigquery
from google.api_core.exceptions import NotFound

from drive_bigquery_loader.config import AppConfig, TargetFileConfig


class BigQueryValidator:
    def __init__(self, config: AppConfig, client: bigquery.Client | None = None) -> None:
        self._config = config
        self._client = client or bigquery.Client(
            project=config.bq_project_id,
            location=config.bq_location,
        )

    def validate_final_tables_exist(self, targets: list[TargetFileConfig]) -> None:
        missing: list[str] = []
        for target in targets:
            table_id = self.final_table_id(target.table_name)
            try:
                self._client.get_table(table_id)
            except NotFound:
                missing.append(table_id)
        if missing:
            raise ValueError(f"Final BigQuery tables are missing: {missing}")

    def validate_final_schema(
        self,
        target: TargetFileConfig,
    ) -> None:
        table = self._client.get_table(self.final_table_id(target.table_name))
        actual_columns = [field.name for field in table.schema]
        if actual_columns != target.bq_columns:
            raise ValueError(
                f"BigQuery schema mismatch for {target.table_name}: "
                f"actual={actual_columns}, expected={target.bq_columns}"
            )

        non_string_fields = [
            f"{field.name}:{field.field_type}:{field.mode}"
            for field in table.schema
            if field.field_type.upper() != "STRING" or field.mode.upper() != "NULLABLE"
        ]
        if non_string_fields:
            raise ValueError(
                f"BigQuery schema must be STRING NULLABLE for {target.table_name}: "
                f"{non_string_fields}"
            )

    def final_table_id(self, table_name: str) -> str:
        return f"{self._config.bq_project_id}.{self._config.bq_dataset_id}.{table_name}"

    def staging_table_id(self, table_name: str, batch_id: str) -> str:
        prefix = self._config.raw["bigquery"]["staging_table_prefix"]
        staging_dataset = self._config.raw["bigquery"]["staging_dataset_id"]
        return (
            f"{self._config.bq_project_id}.{staging_dataset}."
            f"{prefix}{table_name}_{batch_id.replace('-', '_')}"
        )
