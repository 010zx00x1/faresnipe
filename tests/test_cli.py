from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from faresnipe.cli import main, init_config
from faresnipe.config import AppConfig, DetectionConfig, NotificationConfig, ScannerConfig


def _config(db_path: Path) -> AppConfig:
    return AppConfig(
        scanner=ScannerConfig(
            provider="mock",
            database_path=db_path,
            currency="CLP",
            days_ahead_start=7,
            days_ahead_end=7,
            stay_lengths=(7,),
            adults=1,
            max_results_per_search=2,
            request_delay_seconds=0,
            scan_interval_minutes=60,
            scan_jitter_seconds=0,
        ),
        detection=DetectionConfig(
            discount_ratio=Decimal("0.35"),
            mistake_fare_ratio=Decimal("0.55"),
            min_history_quotes=4,
            history_days=180,
        ),
        notifications=NotificationConfig(
            console=False,
            webhook_url=None,
            telegram_bot_token=None,
            telegram_chat_id=None,
        ),
        routes=(),
    )


class CliTest(unittest.TestCase):
    def test_serve_starts_dashboard_with_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            served = []
            with (
                patch("faresnipe.cli.load_config", return_value=_config(Path(tmpdir) / "db.sqlite3")),
                patch("faresnipe.cli.serve_dashboard", side_effect=lambda config, host, port: served.append(config)),
            ):
                main(["serve", "--host", "0.0.0.0", "--port", "9999"])

            self.assertEqual(served[0].scanner.provider_names, ("mock",))

    def test_help_does_not_include_demo_command(self) -> None:
        with self.assertRaises(SystemExit) as exc:
            with patch("sys.stdout") as stdout:
                main(["--help"])

        self.assertEqual(exc.exception.code, 0)
        help_text = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        self.assertNotIn("demo", help_text)

    def test_init_can_filter_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "faresnipe.toml"
            init_config(dest, "SCL", ("EZE",))

            text = dest.read_text(encoding="utf-8")

        self.assertIn('code = "SCL"', text)
        self.assertIn('"EZE"', text)
        self.assertNotIn('"MAD"', text)
        self.assertIn("max_price = 400", text)


if __name__ == "__main__":
    unittest.main()
