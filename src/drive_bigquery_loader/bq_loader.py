from __future__ import annotations

from datetime import datetime, timedelta, timezone

from google.cloud import bigquery

from drive_bigquery_loader.config import AppConfig, TargetFileConfig
from drive_bigquery_loader.models import LoadTarget


class BigQueryLoader:
    def __init__(self, config: AppConfig, client: bigquery.Client | None = None) -> None:
        self._config = config
        self._client = client or bigquery.Client(
            project=config.bq_project_id,
            location=config.bq_location,
        )

    def load_to_staging(
        self,
        load_target: LoadTarget,
        target_config: TargetFileConfig,
        dry_run: bool,
    ) -> None:
        if dry_run:
            return

        job_config = self._build_staging_load_job_config(target_config)
        job = self._client.load_table_from_uri(
            load_target.gcs_uri,
            load_target.staging_table_id,
            job_config=job_config,
            location=self._config.bq_location,
        )
        job.result()

    def _build_staging_load_job_config(
        self,
        target_config: TargetFileConfig,
    ) -> bigquery.LoadJobConfig:
        return bigquery.LoadJobConfig(
            schema=[
                bigquery.SchemaField(name=column, field_type="STRING", mode="NULLABLE")
                for column in target_config.bq_columns
            ],
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            field_delimiter=self._config.raw["csv"]["delimiter"],
            quote_character=self._config.raw["csv"]["quotechar"],
            encoding="UTF-8",
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            write_disposition=self._config.raw["bigquery"]["write_disposition_staging"],
            autodetect=False,
            allow_quoted_newlines=True,
            null_marker="",
        )

    def replace_final_from_staging(
        self,
        load_target: LoadTarget,
        target_config: TargetFileConfig,
        dry_run: bool,
        staging_only: bool,
    ) -> None:
        if dry_run or staging_only:
            return

        columns = ", ".join(f"`{column}`" for column in target_config.bq_columns)
        select_columns = ", ".join(
            f"NULLIF(`{column}`, '') AS `{column}`"
            for column in target_config.bq_columns
        )
        query = f"""
        TRUNCATE TABLE `{load_target.final_table_id}`;

        INSERT INTO `{load_target.final_table_id}` ({columns})
        SELECT {select_columns}
        FROM `{load_target.staging_table_id}`;
        """
        job = self._client.query(query, location=self._config.bq_location)
        job.result()

    def validate_staging_row_count(self, load_target: LoadTarget, expected_rows: int) -> None:
        table = self._client.get_table(load_target.staging_table_id)
        if table.num_rows != expected_rows:
            raise ValueError(
                f"Staging row count mismatch for {load_target.table_name}: "
                f"actual={table.num_rows}, expected={expected_rows}"
            )

    def validate_final_row_count_matches_staging(self, load_target: LoadTarget) -> None:
        staging_rows = self._client.get_table(load_target.staging_table_id).num_rows
        final_rows = self._client.get_table(load_target.final_table_id).num_rows
        if final_rows != staging_rows:
            raise ValueError(
                f"Final row count mismatch for {load_target.table_name}: "
                f"final={final_rows}, staging={staging_rows}"
            )

    def create_backup_tables(
        self,
        load_targets: dict[str, LoadTarget],
        batch_id: str,
        dry_run: bool,
        staging_only: bool,
    ) -> dict[str, str]:
        backup_config = self._config.raw["bigquery"].get("backup", {})
        if dry_run or staging_only or not backup_config.get("enabled", False):
            return {}

        backup_table_ids: dict[str, str] = {}
        for file_name, load_target in load_targets.items():
            backup_table_id = self._backup_table_id(load_target.table_name, batch_id)
            job_config = bigquery.CopyJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            job = self._client.copy_table(
                load_target.final_table_id,
                backup_table_id,
                job_config=job_config,
                location=self._config.bq_location,
            )
            job.result()
            self._apply_backup_expiration(backup_table_id)
            backup_table_ids[file_name] = backup_table_id
        return backup_table_ids

    def restore_final_tables(
        self,
        load_targets: dict[str, LoadTarget],
        backup_table_ids: dict[str, str],
    ) -> None:
        for file_name, backup_table_id in backup_table_ids.items():
            final_table_id = load_targets[file_name].final_table_id
            job_config = bigquery.CopyJobConfig(
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            job = self._client.copy_table(
                backup_table_id,
                final_table_id,
                job_config=job_config,
                location=self._config.bq_location,
            )
            job.result()

    def _backup_table_id(self, table_name: str, batch_id: str) -> str:
        backup_config = self._config.raw["bigquery"].get("backup", {})
        backup_dataset = backup_config.get(
            "dataset_id",
            self._config.raw["bigquery"]["dataset_id"],
        )
        prefix = backup_config.get("table_prefix", "_backup_drive_")
        suffix = batch_id.replace("-", "_")
        return (
            f"{self._config.bq_project_id}.{backup_dataset}."
            f"{prefix}{table_name}_{suffix}"
        )

    def _apply_backup_expiration(self, table_id: str) -> None:
        retention_days = (
            self._config.raw["bigquery"]
            .get("backup", {})
            .get("retention_days")
        )
        if retention_days is None:
            return

        table = self._client.get_table(table_id)
        table.expires = datetime.now(timezone.utc) + timedelta(days=int(retention_days))
        self._client.update_table(table, ["expires"])
