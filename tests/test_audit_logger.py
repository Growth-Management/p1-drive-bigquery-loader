from __future__ import annotations

import sys
import types
import unittest


fake_bigquery = types.SimpleNamespace(Client=object)
sys.modules.setdefault("google", types.ModuleType("google"))
sys.modules.setdefault("google.cloud", types.ModuleType("google.cloud"))
sys.modules["google.cloud"].bigquery = fake_bigquery
sys.modules["google.cloud.bigquery"] = fake_bigquery
sys.modules.setdefault(
    "yaml",
    types.SimpleNamespace(safe_load=lambda stream: {}),
)

from drive_bigquery_loader.audit_logger import AuditLogger
from drive_bigquery_loader.config import AppConfig
from drive_bigquery_loader.models import ValidationResult


class FakeBigQueryClient:
    def __init__(self):
        self.inserted_rows = []

    def insert_rows_json(self, table_id, rows):
        self.inserted_rows.append((table_id, rows))
        return []


class AuditLoggerTest(unittest.TestCase):
    def test_writes_run_audit_record_for_batch_success(self):
        client = FakeBigQueryClient()
        logger = AuditLogger(self._config(), client=client)

        logger.event(
            "batch-1",
            "batch_succeeded",
            {"warnings": ["warning-a", "warning-b"]},
        )

        table_id, rows = client.inserted_rows[0]
        self.assertEqual(table_id, "ice-qb.ice_qb_source.__ingestion_runs")
        self.assertEqual(rows[0]["batch_id"], "batch-1")
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertEqual(rows[0]["warning_count"], 2)

    def test_writes_file_audit_records_for_csv_validation(self):
        client = FakeBigQueryClient()
        logger = AuditLogger(self._config(), client=client)

        logger.event(
            "batch-1",
            "csv_files_validated",
            {
                "results": {
                    "sf_mstrs.csv": ValidationResult(
                        file_name="sf_mstrs.csv",
                        header=["col_a", "col_b"],
                        row_count=10,
                    ),
                    "p1_log_actual.csv": ValidationResult(
                        file_name="p1_log_actual.csv",
                        header=["col_a"],
                        row_count=5,
                        errors=["bad row"],
                    ),
                }
            },
        )

        table_id, rows = client.inserted_rows[0]
        self.assertEqual(table_id, "ice-qb.ice_qb_source.__ingestion_files")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["file_name"], "sf_mstrs.csv")
        self.assertEqual(rows[0]["column_count"], 2)
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[1]["status"], "error")

    def _config(self):
        return AppConfig(
            raw={
                "app": {"environment": "prod"},
                "bigquery": {"project_id": "ice-qb", "location": "US"},
                "audit": {
                    "enable_bigquery_log": True,
                    "fail_on_bigquery_error": False,
                    "run_table": "ice-qb.ice_qb_source.__ingestion_runs",
                    "file_table": "ice-qb.ice_qb_source.__ingestion_files",
                },
            },
            target_files=[],
        )


if __name__ == "__main__":
    unittest.main()
