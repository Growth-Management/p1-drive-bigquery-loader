from __future__ import annotations

from pathlib import Path

from google.cloud import secretmanager
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from drive_bigquery_loader.config import AppConfig
from drive_bigquery_loader.models import DriveFile, LocalCsvFile


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class DriveClient:
    """Small wrapper around Google Drive API v3."""

    def __init__(self, service=None) -> None:
        self._service = service or build("drive", "v3", cache_discovery=False)

    @classmethod
    def from_config(cls, config: AppConfig) -> "DriveClient":
        auth_mode = config.raw.get("drive", {}).get("auth_mode", "application_default")
        if auth_mode == "oauth_refresh_token":
            return cls.from_oauth_refresh_token_config(config)
        if auth_mode == "application_default":
            return cls()
        raise ValueError(f"Unsupported drive.auth_mode: {auth_mode}")

    @classmethod
    def from_oauth_refresh_token_config(cls, config: AppConfig) -> "DriveClient":
        secret_names = config.raw["drive"]["oauth_secret_names"]
        secret_client = secretmanager.SecretManagerServiceClient()
        client_id = _access_secret(
            secret_client,
            config.bq_project_id,
            secret_names["client_id"],
        )
        client_secret = _access_secret(
            secret_client,
            config.bq_project_id,
            secret_names["client_secret"],
        )
        refresh_token = _access_secret(
            secret_client,
            config.bq_project_id,
            secret_names["refresh_token"],
        )
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=client_id,
            client_secret=client_secret,
            scopes=[DRIVE_SCOPE],
        )
        return cls(build("drive", "v3", credentials=credentials, cache_discovery=False))

    @classmethod
    def from_service_account_file(cls, path: str | Path) -> "DriveClient":
        credentials = service_account.Credentials.from_service_account_file(
            str(path),
            scopes=[DRIVE_SCOPE],
        )
        return cls(build("drive", "v3", credentials=credentials, cache_discovery=False))

    def list_folder_files(self, folder_id: str) -> list[DriveFile]:
        query = (
            f"'{folder_id}' in parents and trashed = false "
            "and mimeType != 'application/vnd.google-apps.folder'"
        )
        response = (
            self._service.files()
            .list(
                q=query,
                fields="files(id,name,mimeType,size,modifiedTime)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        return [
            DriveFile(
                id=item["id"],
                name=item["name"],
                mime_type=item.get("mimeType"),
                size=int(item["size"]) if item.get("size") else None,
                modified_time=item.get("modifiedTime"),
            )
            for item in response.get("files", [])
        ]

    def resolve_exact_names(
        self,
        folder_id: str,
        target_names: list[str],
    ) -> dict[str, DriveFile]:
        files = self.list_folder_files(folder_id)
        by_name: dict[str, list[DriveFile]] = {}
        for file in files:
            by_name.setdefault(file.name, []).append(file)

        resolved: dict[str, DriveFile] = {}
        errors: list[str] = []
        for target_name in target_names:
            matches = by_name.get(target_name, [])
            if not matches:
                errors.append(f"Drive file not found: {target_name}")
                continue
            if len(matches) > 1:
                ids = ", ".join(file.id for file in matches)
                errors.append(f"Drive file name is duplicated: {target_name} ({ids})")
                continue
            resolved[target_name] = matches[0]

        if errors:
            raise ValueError("; ".join(errors))
        return resolved

    def find_unexpected_csv_files(
        self,
        folder_id: str,
        target_names: list[str],
    ) -> list[DriveFile]:
        target_name_set = set(target_names)
        return [
            file
            for file in self.list_folder_files(folder_id)
            if file.name.endswith(".csv") and file.name not in target_name_set
        ]

    def download(self, drive_file: DriveFile, output_dir: str | Path) -> LocalCsvFile:
        output_path = Path(output_dir) / drive_file.name
        output_path.parent.mkdir(parents=True, exist_ok=True)

        request = self._service.files().get_media(
            fileId=drive_file.id,
            supportsAllDrives=True,
        )
        with output_path.open("wb") as fp:
            downloader = MediaIoBaseDownload(fp, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        return LocalCsvFile(drive_file=drive_file, path=output_path)


def _access_secret(
    client: secretmanager.SecretManagerServiceClient,
    project_id: str,
    secret_name: str,
) -> str:
    if secret_name.startswith("projects/"):
        version_name = secret_name
    else:
        version_name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": version_name})
    return response.payload.data.decode("utf-8").strip()
