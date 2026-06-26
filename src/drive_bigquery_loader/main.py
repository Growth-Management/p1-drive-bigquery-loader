from __future__ import annotations

import argparse
import logging
import tempfile
import uuid

from drive_bigquery_loader.audit_logger import AuditLogger
from drive_bigquery_loader.bq_loader import BigQueryLoader
from drive_bigquery_loader.bq_validator import BigQueryValidator
from drive_bigquery_loader.config import TargetFileConfig, load_config
from drive_bigquery_loader.csv_validator import CsvValidator
from drive_bigquery_loader.drive_client import DriveClient
from drive_bigquery_loader.gcs_archive import GcsArchive
from drive_bigquery_loader.models import LoadTarget, LocalCsvFile
from drive_bigquery_loader.notifier import Notifier


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load Drive CSV files into BigQuery.")
    parser.add_argument(
        "--config",
        nargs="+",
        default="config/config.yaml",
        help=(
            "Path(s) to config yaml. Later files override earlier files, "
            "for example: --config config/config.yaml config/config.prod.yaml"
        ),
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Optional external batch id. Defaults to a generated UUID.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and planned operations without GCS or BigQuery writes.",
    )
    parser.add_argument(
        "--staging-only",
        action="store_true",
        help="Load staging tables but do not replace final tables.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    args = parse_args()
    batch_id = args.batch_id or str(uuid.uuid4())

    config = load_config(args.config)
    audit = AuditLogger(config)
    notifier = Notifier(config)
    warnings: list[str] = []

    try:
        audit.event(
            batch_id,
            "batch_started",
            {"dry_run": args.dry_run, "staging_only": args.staging_only},
        )
        run_batch(config, batch_id, args.dry_run, args.staging_only, audit, warnings)
        if warnings:
            notifier.notify_warning(batch_id, warnings)
        notifier.notify_success(batch_id, {"staging_only": args.staging_only})
        audit.event(batch_id, "batch_succeeded", {"warnings": warnings})
        return 0
    except Exception as exc:
        logging.exception("Batch failed")
        audit.event(batch_id, "batch_failed", {"error": str(exc)}, severity="ERROR")
        notifier.notify_failure(batch_id, str(exc))
        return 1


def run_batch(
    config,
    batch_id: str,
    dry_run: bool,
    staging_only: bool,
    audit: AuditLogger,
    warnings: list[str],
) -> None:
    target_by_name = {target.file_name: target for target in config.target_files}
    target_names = list(target_by_name)

    drive = DriveClient.from_config(config)
    csv_validator = CsvValidator(config)
    gcs_archive = GcsArchive(config) if not dry_run else None
    bq_validator = BigQueryValidator(config)
    bq_loader = BigQueryLoader(config)

    bq_validator.validate_final_tables_exist(config.target_files)
    for target in config.target_files:
        bq_validator.validate_final_schema(target)
    audit.event(batch_id, "final_tables_validated", {"tables": target_names})

    with tempfile.TemporaryDirectory() as tmpdir:
        unexpected_csv_files = drive.find_unexpected_csv_files(
            config.drive_folder_id,
            target_names,
        )
        if unexpected_csv_files:
            unexpected_names = [file.name for file in unexpected_csv_files]
            policy = config.raw["drive"].get("unexpected_csv_policy", "warn")
            message = f"Unexpected CSV files found in Drive folder: {unexpected_names}"
            if policy == "fail":
                raise ValueError(message)
            if policy == "warn":
                warnings.append(message)
                audit.event(
                    batch_id,
                    "unexpected_csv_files_found",
                    {"files": unexpected_csv_files},
                    severity="WARNING",
                )

        resolved_files = drive.resolve_exact_names(config.drive_folder_id, target_names)
        audit.event(batch_id, "drive_files_resolved", {"files": resolved_files})

        local_files = {
            name: drive.download(drive_file, tmpdir)
            for name, drive_file in resolved_files.items()
        }
        audit.event(batch_id, "drive_files_downloaded", {"files": local_files})

        validation_results = {
            name: csv_validator.validate_file(local_file, target_by_name[name])
            for name, local_file in local_files.items()
        }
        audit.event(batch_id, "csv_files_validated", {"results": validation_results})
        validation_errors = [
            f"{name}: {error}"
            for name, result in validation_results.items()
            for error in result.errors
        ]
        if validation_errors:
            raise ValueError(f"CSV validation failed: {validation_errors}")

        consistency_errors, consistency_warnings = csv_validator.validate_batch_consistency(
            local_files,
            config.target_files,
        )
        warnings.extend(consistency_warnings)
        if consistency_errors:
            raise ValueError(f"Batch consistency validation failed: {consistency_errors}")
        audit.event(
            batch_id,
            "batch_consistency_validated",
            {"warnings": consistency_warnings},
            severity="WARNING" if consistency_warnings else "INFO",
        )

        load_targets = build_load_targets(
            config,
            batch_id,
            local_files,
            target_by_name,
            bq_validator,
            gcs_archive,
            dry_run,
        )
        audit.event(batch_id, "load_targets_built", {"targets": load_targets})

        for target in config.target_files:
            load_target = load_targets[target.file_name]
            bq_loader.load_to_staging(load_target, target, dry_run)
            if not dry_run:
                bq_loader.validate_staging_row_count(
                    load_target,
                    validation_results[target.file_name].row_count,
                )
        audit.event(batch_id, "staging_loaded", {})

        backup_table_ids = bq_loader.create_backup_tables(
            load_targets,
            batch_id,
            dry_run=dry_run,
            staging_only=staging_only,
        )
        audit.event(batch_id, "backup_tables_created", {"tables": backup_table_ids})

        try:
            for target in config.target_files:
                load_target = load_targets[target.file_name]
                bq_loader.replace_final_from_staging(
                    load_target,
                    target,
                    dry_run=dry_run,
                    staging_only=staging_only,
                )
                if not dry_run and not staging_only:
                    bq_loader.validate_final_row_count_matches_staging(load_target)
        except Exception:
            backup_config = config.raw["bigquery"].get("backup", {})
            if backup_config.get("restore_on_failure", True) and backup_table_ids:
                audit.event(
                    batch_id,
                    "final_replace_failed_restore_started",
                    {"backup_tables": backup_table_ids},
                    severity="ERROR",
                )
                bq_loader.restore_final_tables(load_targets, backup_table_ids)
                audit.event(
                    batch_id,
                    "final_tables_restored",
                    {"backup_tables": backup_table_ids},
                )
            raise
        audit.event(batch_id, "final_replaced", {"staging_only": staging_only})


def build_load_targets(
    config,
    batch_id: str,
    local_files: dict[str, LocalCsvFile],
    target_by_name: dict[str, TargetFileConfig],
    bq_validator: BigQueryValidator,
    gcs_archive: GcsArchive | None,
    dry_run: bool,
) -> dict[str, LoadTarget]:
    load_targets: dict[str, LoadTarget] = {}
    for file_name, local_file in local_files.items():
        target = target_by_name[file_name]
        if dry_run:
            gcs_uri = f"dry-run://{local_file.path.name}"
        else:
            if gcs_archive is None:
                raise ValueError("GCS archive is required outside dry-run")
            gcs_uri = gcs_archive.upload_raw_csv(local_file, batch_id)

        load_targets[file_name] = LoadTarget(
            file_name=file_name,
            table_name=target.table_name,
            final_table_id=bq_validator.final_table_id(target.table_name),
            staging_table_id=bq_validator.staging_table_id(target.table_name, batch_id),
            gcs_uri=gcs_uri,
        )
    return load_targets


if __name__ == "__main__":
    raise SystemExit(main())
