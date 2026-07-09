from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from faresnipe.config import AppConfig, DetectionConfig, NotificationConfig, ScannerConfig
from faresnipe.dashboard import DashboardServer
from faresnipe.models import FareQuote, Origin, Route
from faresnipe.storage import FareStore


def _config(db_path: Path) -> AppConfig:
    return AppConfig(
        scanner=ScannerConfig(
            provider="mock", database_path=db_path, currency="CLP",
            days_ahead_start=7, days_ahead_end=7, stay_lengths=(7,),
            adults=1, max_results_per_search=5, request_delay_seconds=0,
            scan_interval_minutes=60, scan_jitter_seconds=0,
        ),
        detection=DetectionConfig(
            discount_ratio=Decimal("0.35"), mistake_fare_ratio=Decimal("0.55"),
            min_history_quotes=4, history_days=180,
        ),
        notifications=NotificationConfig(
            console=False, webhook_url=None,
            telegram_bot_token=None, telegram_chat_id=None,
        ),
        routes=(
            Route(origin="AEP", destination="EZE"),
            Route(origin="SCL", destination="EZE"),
        ),
        origins=(
            Origin(code="AEP", name="Buenos Aires", destinations=("EZE",)),
            Origin(code="SCL", name="Santiago", destinations=("EZE",)),
        ),
    )


class OriginsCompareTest(unittest.TestCase):
    def test_compare_origins_payload_with_multiple_origins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "compare.sqlite3"
            config = _config(db_path)
            store = FareStore(db_path)
            store.save_quote(FareQuote(
                provider="mock", origin="AEP", destination="EZE",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("180000"), currency="CLP", carrier="Carrier A",
                booking_url="https://example.com/aep",
            ))
            store.save_quote(FareQuote(
                provider="mock", origin="SCL", destination="EZE",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("120000"), currency="CLP", carrier="Carrier B",
                booking_url="https://example.com/scl",
            ))
            server = DashboardServer(config=config)

            rows = server.compare_origins_payload("EZE", None, None)

            self.assertEqual([row["origin"] for row in rows], ["SCL", "AEP"])
            self.assertEqual(rows[0]["origin_name"], "Santiago")
            self.assertEqual(rows[0]["cheapest_price"], "120000")
            self.assertEqual(rows[0]["cheapest_carrier"], "Carrier B")
            self.assertEqual(rows[0]["cheapest_booking_url"], "https://example.com/scl")
            self.assertEqual(rows[0]["samples"], 1)

    def test_compare_origins_payload_includes_origin_without_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "compare.sqlite3"
            config = _config(db_path)
            store = FareStore(db_path)
            store.save_quote(FareQuote(
                provider="mock", origin="SCL", destination="EZE",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("120000"), currency="CLP",
            ))
            server = DashboardServer(config=config)

            rows = server.compare_origins_payload("EZE", None, None)

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["origin"], "SCL")
            self.assertEqual(rows[1]["origin"], "AEP")
            self.assertIsNone(rows[1]["cheapest_price"])
            self.assertEqual(rows[1]["samples"], 0)

    def test_compare_origins_payload_sorts_by_price_ascending(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "compare.sqlite3"
            config = _config(db_path)
            store = FareStore(db_path)
            for origin, price in (("AEP", "150000"), ("SCL", "90000")):
                store.save_quote(FareQuote(
                    provider="mock", origin=origin, destination="EZE",
                    departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                    price=Decimal(price), currency="CLP",
                ))
            server = DashboardServer(config=config)

            rows = server.compare_origins_payload("EZE", "2026-08-20", "2026-08-27")

            self.assertEqual([row["origin"] for row in rows], ["SCL", "AEP"])
            self.assertEqual([row["cheapest_price"] for row in rows], ["90000", "150000"])


if __name__ == "__main__":
    unittest.main()
