from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from google.cloud import secretmanager

from drive_bigquery_loader.config import AppConfig


class Notifier:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._logger = logging.getLogger("drive_bigquery_loader.notifier")

    def notify_success(self, batch_id: str, summary: dict[str, Any]) -> None:
        self._send(
            "success",
            {
                "status": "success",
                "batch_id": batch_id,
                "summary": summary,
                "text": self._format_text("success", batch_id, summary),
            },
        )

    def notify_warning(self, batch_id: str, warnings: list[str]) -> None:
        self._send(
            "warning",
            {
                "status": "warning",
                "batch_id": batch_id,
                "warnings": warnings,
                "text": self._format_text("warning", batch_id, {"warnings": warnings}),
            },
        )

    def notify_failure(self, batch_id: str, error: str) -> None:
        self._send(
            "failure",
            {
                "status": "failure",
                "batch_id": batch_id,
                "error": error,
                "text": self._format_text("failure", batch_id, {"error": error}),
            },
        )

    def _send(self, event_name: str, payload: dict[str, Any]) -> None:
        if not self._config.raw["notify"]["enabled"]:
            self._logger.info("Notification disabled: %s", payload)
            return
        if not self._config.raw["notify"].get("notify_on", {}).get(event_name, True):
            self._logger.info("Notification skipped for %s: %s", event_name, payload)
            return

        webhook_url = self._load_webhook_url()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise RuntimeError(f"Notification failed: HTTP {response.status}")

    def _load_webhook_url(self) -> str:
        secret_id = self._config.raw["notify"]["webhook_url_secret_id"]
        client = secretmanager.SecretManagerServiceClient()
        response = client.access_secret_version(name=secret_id)
        return response.payload.data.decode("utf-8")

    def _format_text(
        self,
        status: str,
        batch_id: str,
        details: dict[str, Any],
    ) -> str:
        app_name = self._config.raw.get("app", {}).get("name", "drive-bigquery-loader")
        environment = self._config.raw.get("app", {}).get("environment", "unknown")
        channel = self._config.raw.get("notify", {}).get("channel_name")
        prefix = f"[{app_name}][{environment}][{status}] batch_id={batch_id}"
        if channel:
            prefix = f"{prefix} channel={channel}"
        return f"{prefix} details={json.dumps(details, ensure_ascii=False)}"
