CREATE TABLE IF NOT EXISTS `ice-qb.ice_qb_source.__ingestion_runs` (
  timestamp TIMESTAMP,
  environment STRING,
  batch_id STRING,
  event_type STRING,
  status STRING,
  severity STRING,
  dry_run BOOL,
  staging_only BOOL,
  warning_count INT64,
  error_message STRING,
  payload_json STRING
)
PARTITION BY DATE(timestamp)
CLUSTER BY environment, batch_id, status;

CREATE TABLE IF NOT EXISTS `ice-qb.ice_qb_source.__ingestion_files` (
  timestamp TIMESTAMP,
  environment STRING,
  batch_id STRING,
  file_name STRING,
  row_count INT64,
  column_count INT64,
  status STRING,
  errors_json STRING,
  warnings_json STRING
)
PARTITION BY DATE(timestamp)
CLUSTER BY environment, batch_id, file_name, status;
