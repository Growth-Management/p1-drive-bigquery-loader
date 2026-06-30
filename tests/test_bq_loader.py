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
from drive_bigquery_loader.models import LoadTarget


class FakeQueryJob:
    def result(self):
        return None


class FakeBigQueryClient:
    def __init__(self):
        self.queries = []

    def query(self, query, location=None):
        self.queries.append((query, location))
        return FakeQueryJob()


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

    def test_final_replace_nulls_empty_strings(self):
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
            file_name="sf_mstrs.csv",
            table_name="sf_mstrs",
            duplicate_policy="allow",
            duplicate_key_columns=[],
            expected_header=["col_a", "col_b"],
            bq_columns=["col_a", "col_b"],
        )
        load_target = LoadTarget(
            file_name="sf_mstrs.csv",
            table_name="sf_mstrs",
            final_table_id="ice-qb.ice_qb_source.sf_mstrs",
            staging_table_id="ice-qb.ice_qb_source._stg_drive_sf_mstrs_test",
            gcs_uri="gs://bucket/sf_mstrs.csv",
        )
        client = FakeBigQueryClient()

        BigQueryLoader(
            app_config,
            client=client,
        ).replace_final_from_staging(
            load_target,
            target_config,
            dry_run=False,
            staging_only=False,
        )

        query, location = client.queries[0]
        self.assertEqual(location, "US")
        self.assertIn("NULLIF(`col_a`, '') AS `col_a`", query)
        self.assertIn("NULLIF(`col_b`, '') AS `col_b`", query)
        self.assertIn("INSERT INTO `ice-qb.ice_qb_source.sf_mstrs`", query)


if __name__ == "__main__":
    unittest.main()
