from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    mime_type: str | None = None
    size: int | None = None
    modified_time: str | None = None


@dataclass(frozen=True)
class LocalCsvFile:
    drive_file: DriveFile
    path: Path


@dataclass
class ValidationResult:
    file_name: str
    header: list[str]
    row_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class LoadTarget:
    file_name: str
    table_name: str
    final_table_id: str
    staging_table_id: str
    gcs_uri: str
