from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from faresnipe.config import AppConfig, DetectionConfig, NotificationConfig, ScannerConfig
from faresnipe.models import FareQuote, Route, SearchQuery
from faresnipe.notify import Notifier
from faresnipe.scanner import FlightScanner
from faresnipe.storage import FareStore


class FixedProvider:
    name = "fixed"

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        return [
            FareQuote(
                provider=self.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=Decimal("49"),
                currency=query.currency,
                carrier="XX",
            )
        ]


class FailingThenFixedProvider:
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary provider failure")
        return [
            FareQuote(
                provider=self.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=Decimal("59"),
                currency=query.currency,
                carrier="YY",
            )
        ]


class NamedProvider:
    def __init__(self, name: str, price: str) -> None:
        self.name = name
        self.price = Decimal(price)

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        return [
            FareQuote(
                provider=self.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=self.price,
                currency=query.currency,
                carrier=self.name.upper(),
            )
        ]


class CapturingNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__(console=False)
        self.alerts = []

    def send(self, alert) -> None:  # type: ignore[no-untyped-def]
        self.alerts.append(alert)


class FlightScannerTest(unittest.TestCase):
    def test_scanner_saves_quotes_and_sends_threshold_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "faresnipe.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="fixed",
                    database_path=db_path,
                    currency="USD",
                    days_ahead_start=7,
                    days_ahead_end=7,
                    stay_lengths=(4,),
                    adults=1,
                    max_results_per_search=3,
                    request_delay_seconds=0,
                    scan_interval_minutes=30,
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
                routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("80")),),
            )
            notifier = CapturingNotifier()
            scanner = FlightScanner(
                config=config,
                provider=FixedProvider(),
                store=FareStore(db_path),
                notifier=notifier,
            )

            stats = scanner.run_once()

            self.assertEqual(stats.searches, 1)
            self.assertEqual(stats.quotes, 1)
            self.assertEqual(stats.alerts, 1)
            self.assertEqual(len(notifier.alerts), 1)
            self.assertEqual(notifier.alerts[0].quote.origin, "AEP")
            self.assertEqual(notifier.alerts[0].quote.destination, "SCL")
            self.assertEqual(notifier.alerts[0].quote.price, Decimal("49"))

    def test_scanner_emits_progress_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "faresnipe.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="fixed",
                    database_path=db_path,
                    currency="USD",
                    days_ahead_start=7,
                    days_ahead_end=8,
                    stay_lengths=(4,),
                    adults=1,
                    max_results_per_search=3,
                    request_delay_seconds=0,
                    scan_interval_minutes=30,
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
                routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("80")),),
            )
            events = []
            scanner = FlightScanner(
                config=config,
                provider=FixedProvider(),
                store=FareStore(db_path),
                notifier=CapturingNotifier(),
            )

            stats = scanner.run_once(limit_searches=1, progress_callback=events.append)

            self.assertEqual(stats.searches, 1)
            self.assertEqual([event["event"] for event in events], ["search_started", "search_completed"])
            self.assertEqual(events[0]["searches"], 0)
            self.assertEqual(events[1]["searches"], 1)
            self.assertEqual(events[0]["total_searches"], 1)

    def test_scanner_queries_each_provider_for_same_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "faresnipe.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="alpha",
                    providers=("alpha", "beta"),
                    database_path=db_path,
                    currency="USD",
                    days_ahead_start=7,
                    days_ahead_end=7,
                    stay_lengths=(4,),
                    adults=1,
                    max_results_per_search=3,
                    request_delay_seconds=0,
                    scan_interval_minutes=30,
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
                routes=(Route(origin="AEP", destination="SCL"),),
            )
            store = FareStore(db_path)
            scanner = FlightScanner(
                config=config,
                providers=(NamedProvider("alpha", "49"), NamedProvider("beta", "59")),
                store=store,
                notifier=CapturingNotifier(),
            )

            stats = scanner.run_once()
            recent = list(store.recent_quotes(10))

            self.assertEqual(stats.searches, 2)
            self.assertEqual(stats.quotes, 2)
            self.assertEqual(scanner.planned_searches(), 2)
            self.assertEqual({row["provider"] for row in recent}, {"alpha", "beta"})

    def test_limited_scan_spreads_first_pass_across_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "faresnipe.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="fixed",
                    database_path=db_path,
                    currency="USD",
                    days_ahead_start=7,
                    days_ahead_end=8,
                    stay_lengths=(4,),
                    adults=1,
                    max_results_per_search=3,
                    request_delay_seconds=0,
                    scan_interval_minutes=30,
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
                routes=(
                    Route(origin="SCL", destination="EZE"),
                    Route(origin="SCL", destination="LIM"),
                ),
            )
            store = FareStore(db_path)
            scanner = FlightScanner(
                config=config,
                provider=FixedProvider(),
                store=store,
                notifier=CapturingNotifier(),
            )

            stats = scanner.run_once(limit_searches=2)
            recent = list(store.recent_quotes(10))

            self.assertEqual(stats.searches, 2)
            self.assertEqual({row["destination"] for row in recent}, {"EZE", "LIM"})

    def test_limited_scan_does_not_sleep_after_final_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "faresnipe.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="fixed",
                    database_path=db_path,
                    currency="USD",
                    days_ahead_start=7,
                    days_ahead_end=7,
                    stay_lengths=(4,),
                    adults=1,
                    max_results_per_search=3,
                    request_delay_seconds=45,
                    scan_interval_minutes=30,
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
                routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("80")),),
            )
            scanner = FlightScanner(
                config=config,
                provider=FixedProvider(),
                store=FareStore(db_path),
                notifier=CapturingNotifier(),
            )

            with patch("faresnipe.scanner.time.sleep") as sleep:
                stats = scanner.run_once(limit_searches=1)

            self.assertEqual(stats.searches, 1)
            sleep.assert_not_called()

    def test_scanner_continues_after_single_search_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "faresnipe.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="flaky",
                    database_path=db_path,
                    currency="USD",
                    days_ahead_start=7,
                    days_ahead_end=8,
                    stay_lengths=(4,),
                    adults=1,
                    max_results_per_search=3,
                    request_delay_seconds=0,
                    scan_interval_minutes=30,
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
                routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("80")),),
            )
            events = []
            scanner = FlightScanner(
                config=config,
                provider=FailingThenFixedProvider(),
                store=FareStore(db_path),
                notifier=CapturingNotifier(),
            )

            stats = scanner.run_once(progress_callback=events.append)

            self.assertEqual(stats.searches, 2)
            self.assertEqual(stats.quotes, 1)
            self.assertEqual(stats.failures, 1)
            self.assertIn("search_failed", [event["event"] for event in events])
            failed = [event for event in events if event["event"] == "search_failed"][0]
            self.assertEqual(failed["failures"], 1)
            self.assertIn("temporary provider failure", failed["error"])


if __name__ == "__main__":
    unittest.main()
