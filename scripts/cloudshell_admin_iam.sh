#!/usr/bin/env bash
set -euo pipefail

# Run this with an account that can update IAM policies on project ice-qb.
# This script grants deployment/runtime permissions needed by p1-drive-bigquery-loader.

PROJECT_ID="${PROJECT_ID:-ice-qb}"
GITHUB_OWNER="${GITHUB_OWNER:-Growth-Management}"
GITHUB_REPO="${GITHUB_REPO:-p1-drive-bigquery-loader}"
RUNTIME_SA_ID="${RUNTIME_SA_ID:-p1-drive-bigquery-loader}"
DEPLOY_SA_ID="${DEPLOY_SA_ID:-gh-p1-drive-bq-loader}"
WIF_POOL_ID="${WIF_POOL_ID:-github-actions}"
ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-ice-qb-p1-drive-bigquery-loader-archive}"
SLACK_SECRET_ID="${SLACK_SECRET_ID:-slack-webhook-ice-adm-system-alerts}"

RUNTIME_SA="${RUNTIME_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="${DEPLOY_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT_ID}"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo "Granting deploy service account project roles..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOY_SA}" \
  --role "roles/run.admin" \
  --condition=None

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOY_SA}" \
  --role "roles/artifactregistry.writer" \
  --condition=None

echo "Granting deploy service account permission to use runtime service account..."
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member "serviceAccount:${DEPLOY_SA}" \
  --role "roles/iam.serviceAccountUser"

echo "Granting GitHub WIF principal permission to impersonate deploy service account..."
WIF_PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL_ID}/attribute.repository/${GITHUB_OWNER}/${GITHUB_REPO}"
gcloud iam service-accounts add-iam-policy-binding "${DEPLOY_SA}" \
  --member "${WIF_PRINCIPAL}" \
  --role "roles/iam.workloadIdentityUser"

echo "Granting runtime service account BigQuery roles..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/bigquery.jobUser" \
  --condition=None

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/bigquery.dataViewer" \
  --condition=None

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/bigquery.dataEditor" \
  --condition=None

echo "Granting runtime service account archive bucket writer role..."
gcloud storage buckets add-iam-policy-binding "gs://${ARCHIVE_BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/storage.objectCreator"

echo "Granting runtime service account Slack secret accessor role..."
gcloud secrets add-iam-policy-binding "${SLACK_SECRET_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/secretmanager.secretAccessor"

echo
echo "Admin IAM setup complete."
echo "Next: rerun scripts/cloudshell_bootstrap.sh with SKIP_IAM_BINDINGS=true."
