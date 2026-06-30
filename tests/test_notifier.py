from __future__ import annotations

import sys
import types
import unittest


fake_secretmanager = types.SimpleNamespace(SecretManagerServiceClient=object)
sys.modules.setdefault("google", types.ModuleType("google"))
sys.modules.setdefault("google.cloud", types.ModuleType("google.cloud"))
sys.modules["google.cloud"].secretmanager = fake_secretmanager
sys.modules["google.cloud.secretmanager"] = fake_secretmanager
sys.modules.setdefault(
    "yaml",
    types.SimpleNamespace(safe_load=lambda stream: {}),
)

from drive_bigquery_loader.config import AppConfig
from drive_bigquery_loader.notifier import Notifier


class CapturingNotifier(Notifier):
    def __init__(self, config):
        super().__init__(config)
        self.sent = []

    def _send(self, event_name, payload):
        self.sent.append((event_name, payload))


class NotifierTest(unittest.TestCase):
    def test_skips_warning_notification_when_all_warnings_are_suppressed(self):
        notifier = CapturingNotifier(self._config())

        notifier.notify_warning(
            "batch-1",
            [
                (
                    "p1_log_actual.csv: title_detail_code has values not found "
                    "in reference: count=1, sample=['2316320006']"
                ),
                (
                    "p1_log_detail.csv: sales_date_yyyymmdd month has values "
                    "not found in reference: count=122"
                ),
            ],
        )

        self.assertEqual(notifier.sent, [])

    def test_warning_notification_keeps_unknown_warnings_and_formats_lines(self):
        notifier = CapturingNotifier(self._config())

        notifier.notify_warning(
            "batch-1",
            [
                (
                    "p1_log_actual.csv: title_detail_code has values not found "
                    "in reference: count=1, sample=['2316320006']"
                ),
                "new warning that should be sent",
            ],
        )

        event_name, payload = notifier.sent[0]
        self.assertEqual(event_name, "warning")
        self.assertEqual(payload["warnings"], ["new warning that should be sent"])
        self.assertEqual(payload["suppressed_warning_count"], 1)
        self.assertIn("\nwarnings:\n1. new warning that should be sent", payload["text"])
        self.assertIn("\nsuppressed_known_warnings: 1", payload["text"])

    def test_failure_notification_formats_error_on_separate_lines(self):
        notifier = CapturingNotifier(self._config())

        notifier.notify_failure("batch-1", "line 1\nline 2")

        event_name, payload = notifier.sent[0]
        self.assertEqual(event_name, "failure")
        self.assertIn("\nerror:\nline 1\nline 2", payload["text"])

    def _config(self):
        return AppConfig(
            raw={
                "app": {
                    "name": "p1-drive-bigquery-loader",
                    "environment": "prod",
                },
                "notify": {
                    "channel_name": "ice_adm_system_alerts",
                    "suppress_warning_contains": [
                        (
                            "title_detail_code has values not found in reference: "
                            "count=1, sample=['2316320006']"
                        ),
                        "sales_date_yyyymmdd month has values not found in reference",
                    ],
                },
            },
            target_files=[],
        )


if __name__ == "__main__":
    unittest.main()
