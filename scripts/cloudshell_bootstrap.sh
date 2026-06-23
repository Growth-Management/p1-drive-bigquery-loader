#!/usr/bin/env bash
set -euo pipefail

# Cloud Shell bootstrap for p1-drive-bigquery-loader.
# Usage:
#   export GITHUB_OWNER="Growth-Management"
#   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
#   bash scripts/cloudshell_bootstrap.sh

PROJECT_ID="${PROJECT_ID:-ice-qb}"
REGION="${REGION:-asia-northeast1}"
GITHUB_REPO="${GITHUB_REPO:-p1-drive-bigquery-loader}"
GITHUB_OWNER="${GITHUB_OWNER:-}"

ARTIFACT_REPO="${ARTIFACT_REPO:-p1-drive-bigquery-loader}"
IMAGE_NAME="${IMAGE_NAME:-p1-drive-bigquery-loader}"
CLOUD_RUN_JOB_NAME="${CLOUD_RUN_JOB_NAME:-p1-drive-bigquery-loader}"

RUNTIME_SA_ID="${RUNTIME_SA_ID:-p1-drive-bigquery-loader}"
DEPLOY_SA_ID="${DEPLOY_SA_ID:-github-actions-p1-drive-bigquery-loader}"
WIF_POOL_ID="${WIF_POOL_ID:-github-actions}"
WIF_PROVIDER_ID="${WIF_PROVIDER_ID:-p1-drive-bigquery-loader}"

ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-ice-qb-p1-drive-bigquery-loader-archive}"
SLACK_SECRET_ID="${SLACK_SECRET_ID:-slack-webhook-ice-adm-system-alerts}"
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"

if [[ -z "${GITHUB_OWNER}" ]]; then
  echo "GITHUB_OWNER is required. Example: export GITHUB_OWNER=Growth-Management" >&2
  exit 1
fi

gcloud config set project "${PROJECT_ID}"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"

echo "Enabling required APIs..."
gcloud services enable \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com

echo "Creating Artifact Registry repository if missing..."
if ! gcloud artifacts repositories describe "${ARTIFACT_REPO}" \
  --location "${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${ARTIFACT_REPO}" \
    --repository-format docker \
    --location "${REGION}" \
    --description "Docker images for ${GITHUB_REPO}"
fi

echo "Creating service accounts if missing..."
if ! gcloud iam service-accounts describe \
  "${RUNTIME_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${RUNTIME_SA_ID}" \
    --display-name "Runtime SA for ${GITHUB_REPO}"
fi

if ! gcloud iam service-accounts describe \
  "${DEPLOY_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${DEPLOY_SA_ID}" \
    --display-name "GitHub Actions deploy SA for ${GITHUB_REPO}"
fi

RUNTIME_SA="${RUNTIME_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="${DEPLOY_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Granting deploy service account IAM..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOY_SA}" \
  --role "roles/run.admin" \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${DEPLOY_SA}" \
  --role "roles/artifactregistry.writer" \
  --condition=None >/dev/null

gcloud iam service-accounts add-iam-policy-binding "${RUNTIME_SA}" \
  --member "serviceAccount:${DEPLOY_SA}" \
  --role "roles/iam.serviceAccountUser" >/dev/null

echo "Creating archive bucket if missing..."
if ! gcloud storage buckets describe "gs://${ARCHIVE_BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${ARCHIVE_BUCKET}" \
    --project "${PROJECT_ID}" \
    --location "${REGION}" \
    --uniform-bucket-level-access
fi

echo "Granting runtime service account IAM..."
gcloud storage buckets add-iam-policy-binding "gs://${ARCHIVE_BUCKET}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/storage.objectCreator" >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/bigquery.jobUser" \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/bigquery.dataViewer" \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/bigquery.dataEditor" \
  --condition=None >/dev/null

echo "Creating Slack webhook secret if missing..."
if ! gcloud secrets describe "${SLACK_SECRET_ID}" >/dev/null 2>&1; then
  gcloud secrets create "${SLACK_SECRET_ID}" --replication-policy automatic
fi

if [[ -n "${SLACK_WEBHOOK_URL}" ]]; then
  printf '%s' "${SLACK_WEBHOOK_URL}" | gcloud secrets versions add "${SLACK_SECRET_ID}" --data-file=-
else
  echo "SLACK_WEBHOOK_URL is empty. Secret was created/confirmed, but no new version was added."
fi

gcloud secrets add-iam-policy-binding "${SLACK_SECRET_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role "roles/secretmanager.secretAccessor" >/dev/null

echo "Creating Workload Identity Federation pool/provider if missing..."
if ! gcloud iam workload-identity-pools describe "${WIF_POOL_ID}" \
  --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${WIF_POOL_ID}" \
    --location global \
    --display-name "GitHub Actions"
fi

if ! gcloud iam workload-identity-pools providers describe "${WIF_PROVIDER_ID}" \
  --workload-identity-pool "${WIF_POOL_ID}" \
  --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${WIF_PROVIDER_ID}" \
    --workload-identity-pool "${WIF_POOL_ID}" \
    --location global \
    --display-name "${GITHUB_REPO}" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '${GITHUB_OWNER}/${GITHUB_REPO}'"
fi

WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL_ID}/providers/${WIF_PROVIDER_ID}"
WIF_PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL_ID}/attribute.repository/${GITHUB_OWNER}/${GITHUB_REPO}"

gcloud iam service-accounts add-iam-policy-binding "${DEPLOY_SA}" \
  --member "${WIF_PRINCIPAL}" \
  --role "roles/iam.workloadIdentityUser" >/dev/null

echo
echo "Bootstrap complete."
echo
echo "Set these GitHub environment/repository secrets:"
echo "GCP_WORKLOAD_IDENTITY_PROVIDER=${WIF_PROVIDER}"
echo "GCP_DEPLOY_SERVICE_ACCOUNT=${DEPLOY_SA}"
echo
echo "Confirm runtime config values:"
echo "archive_bucket=${ARCHIVE_BUCKET}"
echo "slack_secret=projects/${PROJECT_ID}/secrets/${SLACK_SECRET_ID}/versions/latest"
