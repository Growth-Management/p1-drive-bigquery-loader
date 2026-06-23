#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-ice-qb}"
REGION="${REGION:-asia-northeast1}"
ARTIFACT_REPO="${ARTIFACT_REPO:-p1-drive-bigquery-loader}"
CLOUD_RUN_JOB_NAME="${CLOUD_RUN_JOB_NAME:-p1-drive-bigquery-loader}"
RUNTIME_SA_ID="${RUNTIME_SA_ID:-p1-drive-bigquery-loader}"
DEPLOY_SA_ID="${DEPLOY_SA_ID:-gh-p1-drive-bq-loader}"
ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-ice-qb-p1-drive-bigquery-loader-archive}"
SLACK_SECRET_ID="${SLACK_SECRET_ID:-slack-webhook-ice-adm-system-alerts}"

gcloud config set project "${PROJECT_ID}" >/dev/null

echo "Project:"
gcloud projects describe "${PROJECT_ID}" --format="table(projectId,projectNumber,name)"

echo
echo "Artifact Registry:"
gcloud artifacts repositories describe "${ARTIFACT_REPO}" \
  --location "${REGION}" \
  --format="yaml(name,format,location)"

echo
echo "Service accounts:"
gcloud iam service-accounts describe \
  "${RUNTIME_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --format="yaml(email,displayName)"
gcloud iam service-accounts describe \
  "${DEPLOY_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --format="yaml(email,displayName)"

echo
echo "Archive bucket:"
gcloud storage buckets describe "gs://${ARCHIVE_BUCKET}" \
  --format="yaml(name,location,uniformBucketLevelAccess)"

echo
echo "Slack secret:"
gcloud secrets describe "${SLACK_SECRET_ID}" --format="yaml(name,replication)"
gcloud secrets versions list "${SLACK_SECRET_ID}" --limit=5

echo
echo "Cloud Run Job:"
if gcloud run jobs describe "${CLOUD_RUN_JOB_NAME}" \
  --region "${REGION}" >/dev/null 2>&1; then
  gcloud run jobs describe "${CLOUD_RUN_JOB_NAME}" \
    --region "${REGION}" \
    --format="yaml(metadata.name,spec.template.template.spec.serviceAccountName,spec.template.template.spec.containers[0].image)"
else
  echo "Cloud Run Job is not deployed yet. This is expected before the first GitHub Actions deploy."
fi
