from __future__ import annotations

import sys
import types
import unittest


class FakeLoadJobConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeSchemaField:
    def __init__(self, name, field_type, mode):
        self.name = name
        self.field_type = field_type
        self.mode = mode


fake_bigquery = types.SimpleNamespace(
    Client=object,
    CopyJobConfig=object,
    CreateDisposition=types.SimpleNamespace(CREATE_IF_NEEDED="CREATE_IF_NEEDED"),
    LoadJobConfig=FakeLoadJobConfig,
    SchemaField=FakeSchemaField,
    SourceFormat=types.SimpleNamespace(CSV="CSV"),
    WriteDisposition=types.SimpleNamespace(WRITE_TRUNCATE="WRITE_TRUNCATE"),
)
sys.modules.setdefault("google", types.ModuleType("google"))
sys.modules.setdefault("google.cloud", types.ModuleType("google.cloud"))
sys.modules["google.cloud"].bigquery = fake_bigquery
sys.modules["google.cloud.bigquery"] = fake_bigquery
sys.modules.setdefault(
    "yaml",
    types.SimpleNamespace(safe_load=lambda stream: {}),
)

from drive_bigquery_loader.bq_loader import BigQueryLoader
from drive_bigquery_loader.config import AppConfig, TargetFileConfig


class BigQueryLoaderTest(unittest.TestCase):
    def test_staging_load_job_config_treats_empty_fields_as_null(self):
        app_config = AppConfig(
            raw={
                "csv": {"delimiter": ",", "quotechar": '"'},
                "bigquery": {
                    "project_id": "ice-qb",
                    "location": "US",
                    "write_disposition_staging": "WRITE_TRUNCATE",
                },
            },
            target_files=[],
        )
        target_config = TargetFileConfig(
            file_name="p1_log_actual.csv",
            table_name="p1_log_actual",
            duplicate_policy="allow",
            duplicate_key_columns=[],
            expected_header=["col_a", "col_b"],
            bq_columns=["col_a", "col_b"],
        )

        job_config = BigQueryLoader(
            app_config,
            client=object(),
        )._build_staging_load_job_config(target_config)

        self.assertEqual(job_config.null_marker, "")
        self.assertFalse(job_config.autodetect)
        self.assertEqual(job_config.source_format, "CSV")
        self.assertEqual(
            [field.mode for field in job_config.schema],
            ["NULLABLE", "NULLABLE"],
        )


if __name__ == "__main__":
    unittest.main()
