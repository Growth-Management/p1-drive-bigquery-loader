from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path

from drive_bigquery_loader.config import AppConfig, TargetFileConfig
from drive_bigquery_loader.models import LocalCsvFile, ValidationResult


class CsvValidator:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._csv_config = config.raw["csv"]
        self._batch_config = config.raw.get("batch", {})

    def validate_file(
        self,
        local_file: LocalCsvFile,
        target_config: TargetFileConfig,
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        row_count = 0
        header: list[str] = []

        try:
            with local_file.path.open(
                "r",
                encoding=self._csv_config["encoding"],
                newline="",
            ) as fp:
                reader = csv.reader(
                    fp,
                    delimiter=self._csv_config["delimiter"],
                    quotechar=self._csv_config["quotechar"],
                )
                header = next(reader)
                self._validate_header(header, target_config.expected_header, errors)
                row_count = self._validate_rows(
                    reader,
                    header,
                    target_config,
                    errors,
                )
        except UnicodeDecodeError as exc:
            errors.append(f"CSV encoding error: {exc}")
        except StopIteration:
            errors.append("CSV is empty")

        return ValidationResult(
            file_name=local_file.drive_file.name,
            header=header,
            row_count=row_count,
            errors=errors,
            warnings=warnings,
        )

    def validate_batch_consistency(
        self,
        files: dict[str, LocalCsvFile],
        targets: list[TargetFileConfig],
    ) -> list[str]:
        """Validate cross-file consistency before staging."""
        errors: list[str] = []
        target_by_name = {target.file_name: target for target in targets}
        errors.extend(self._validate_modified_time_window(files))

        for file_name, local_file in files.items():
            target = target_by_name[file_name]
            header = self._read_header(local_file.path)
            missing_keys = [
                key
                for key in target.duplicate_key_columns
                if target.source_column_for_bq(key) not in header
            ]
            if missing_keys:
                errors.append(f"{file_name}: missing key columns: {missing_keys}")

        if errors:
            return errors

        self._validate_title_detail_code_reference(errors, files, target_by_name)
        self._validate_actual_detail_month(errors, files, target_by_name)
        return errors

    def _validate_header(
        self,
        actual_header: list[str],
        expected_header: list[str],
        errors: list[str],
    ) -> None:
        duplicate_columns = [
            column for column, count in Counter(actual_header).items() if count > 1
        ]
        if duplicate_columns:
            errors.append(f"Duplicate columns found: {duplicate_columns}")

        if actual_header != expected_header:
            errors.append(
                "CSV header does not match expected schema. "
                f"actual={actual_header}, expected={expected_header}"
            )

    def _validate_rows(
        self,
        reader: csv.reader,
        header: list[str],
        target_config: TargetFileConfig,
        errors: list[str],
    ) -> int:
        row_count = 0
        header_len = len(header)
        max_errors = int(self._csv_config.get("max_validation_errors", 20))
        key_indexes = self._key_indexes(header, target_config, errors)
        seen_keys: dict[tuple[str, ...], int] = {}
        duplicate_counts: Counter[tuple[str, ...]] = Counter()
        blank_key_policy = self._csv_config.get("duplicate_key_blank_policy", "error")
        require_non_empty_keys = (
            self._batch_config.get("consistency", {})
            .get("require_non_empty_business_keys", True)
        )
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            if len(row) != header_len:
                errors.append(
                    f"Column count mismatch at row {row_number}: "
                    f"actual={len(row)}, expected={header_len}"
                )
                if len(errors) >= max_errors:
                    errors.append("Too many row validation errors; stopped early")
                    break
                continue

            if key_indexes:
                key = tuple(row[index].strip() for index in key_indexes)
                if any(value == "" for value in key):
                    if require_non_empty_keys or blank_key_policy == "error":
                        errors.append(
                            f"Blank business key at row {row_number}: "
                            f"columns={target_config.duplicate_key_columns}, values={key}"
                        )
                        if len(errors) >= max_errors:
                            errors.append("Too many row validation errors; stopped early")
                            break
                    elif blank_key_policy == "ignore":
                        continue

                if target_config.duplicate_policy == "error":
                    if key in seen_keys:
                        duplicate_counts[key] += 1
                    else:
                        seen_keys[key] = row_number

        if duplicate_counts:
            samples = [
                {
                    "values": key,
                    "first_row": seen_keys[key],
                    "duplicate_count": count,
                }
                for key, count in duplicate_counts.most_common(20)
            ]
            errors.append(
                f"Duplicate keys found: columns={target_config.duplicate_key_columns}, "
                f"unique_duplicate_keys={len(duplicate_counts)}, samples={samples}"
            )
        return row_count

    def _read_header(self, path: Path) -> list[str]:
        with path.open("r", encoding=self._csv_config["encoding"], newline="") as fp:
            return next(
                csv.reader(
                    fp,
                    delimiter=self._csv_config["delimiter"],
                    quotechar=self._csv_config["quotechar"],
                )
            )

    def _key_indexes(
        self,
        header: list[str],
        target_config: TargetFileConfig,
        errors: list[str],
    ) -> list[int]:
        indexes: list[int] = []
        for bq_column in target_config.key_columns:
            source_column = target_config.source_column_for_bq(bq_column)
            if source_column not in header:
                errors.append(
                    f"Key column is missing from CSV header: "
                    f"bq_column={bq_column}, source_column={source_column}"
                )
                continue
            indexes.append(header.index(source_column))
        return indexes

    def _read_column_values(self, path: Path, column: str) -> set[str]:
        values: set[str] = set()
        with path.open("r", encoding=self._csv_config["encoding"], newline="") as fp:
            reader = csv.DictReader(
                fp,
                delimiter=self._csv_config["delimiter"],
                quotechar=self._csv_config["quotechar"],
            )
            for row in reader:
                value = (row.get(column) or "").strip()
                if value:
                    values.add(value)
        return values

    def _read_bq_column_values(
        self,
        path: Path,
        target_config: TargetFileConfig,
        bq_column: str,
    ) -> set[str]:
        return self._read_column_values(
            path,
            target_config.source_column_for_bq(bq_column),
        )

    def _append_missing_reference_error(
        self,
        errors: list[str],
        file_name: str,
        column_name: str,
        child_values: set[str],
        parent_values: set[str],
    ) -> None:
        missing_values = sorted(child_values - parent_values)
        if missing_values:
            sample = missing_values[:20]
            errors.append(
                f"{file_name}: {column_name} has values not found in reference: "
                f"count={len(missing_values)}, sample={sample}"
            )

    def _validate_modified_time_window(
        self,
        files: dict[str, LocalCsvFile],
    ) -> list[str]:
        max_diff = self._batch_config.get("max_modified_time_diff_seconds")
        if max_diff is None:
            return []

        parsed: list[tuple[str, datetime]] = []
        for file_name, local_file in files.items():
            modified_time = local_file.drive_file.modified_time
            if not modified_time:
                return [f"{file_name}: Drive modifiedTime is missing"]
            parsed.append((file_name, self._parse_drive_time(modified_time)))

        min_file, min_time = min(parsed, key=lambda item: item[1])
        max_file, max_time = max(parsed, key=lambda item: item[1])
        diff_seconds = int((max_time - min_time).total_seconds())
        if diff_seconds > int(max_diff):
            return [
                "Drive modifiedTime window exceeded: "
                f"actual_seconds={diff_seconds}, allowed_seconds={max_diff}, "
                f"oldest={min_file}:{min_time.isoformat()}, "
                f"newest={max_file}:{max_time.isoformat()}"
            ]
        return []

    def _parse_drive_time(self, value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _validate_title_detail_code_reference(
        self,
        errors: list[str],
        files: dict[str, LocalCsvFile],
        target_by_name: dict[str, TargetFileConfig],
    ) -> None:
        check_config = (
            self._batch_config.get("consistency", {})
            .get("title_detail_code_reference", {})
        )
        if not check_config.get("enabled", True):
            return

        master_file = check_config["master_file"]
        master_key_column = check_config["master_key_column"]
        master_values = self._read_bq_column_values(
            files[master_file].path,
            target_by_name[master_file],
            master_key_column,
        )

        for child in check_config.get("child_files", []):
            child_file = child["file_name"]
            child_key_column = child["key_column"]
            child_values = self._read_bq_column_values(
                files[child_file].path,
                target_by_name[child_file],
                child_key_column,
            )
            self._append_missing_reference_error(
                errors,
                child_file,
                child_key_column,
                child_values,
                master_values,
            )

    def _validate_actual_detail_month(
        self,
        errors: list[str],
        files: dict[str, LocalCsvFile],
        target_by_name: dict[str, TargetFileConfig],
    ) -> None:
        check_config = (
            self._batch_config.get("consistency", {})
            .get("actual_detail_month", {})
        )
        if not check_config.get("enabled", True):
            return

        actual_file = check_config["actual_file"]
        detail_file = check_config["detail_file"]
        actual_months = self._read_bq_column_values(
            files[actual_file].path,
            target_by_name[actual_file],
            check_config["actual_month_column"],
        )
        detail_days = self._read_bq_column_values(
            files[detail_file].path,
            target_by_name[detail_file],
            check_config["detail_day_column"],
        )
        detail_months = {value[:6] for value in detail_days if len(value) >= 6}
        self._append_missing_reference_error(
            errors,
            detail_file,
            f"{check_config['detail_day_column']} month",
            detail_months,
            actual_months,
        )
