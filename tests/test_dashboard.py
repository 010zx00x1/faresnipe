from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from faresnipe.config import AppConfig, DetectionConfig, NotificationConfig, ScannerConfig
from faresnipe.dashboard import DashboardServer, serve_dashboard
from faresnipe.dashboard.server import _build_handler
from faresnipe.models import Baseline, DealAlert, FareQuote, Origin, Route, SearchQuery
from faresnipe.storage import FareStore


class DashboardFlakyProvider:
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("google blocked route")
        return [
            FareQuote(
                provider=self.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=Decimal("100000"),
                currency=query.currency,
            )
        ]


class DashboardOriginProvider:
    name = "origin_provider"

    def __init__(self) -> None:
        self.origins = []

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        self.origins.append(query.origin)
        return [
            FareQuote(
                provider=self.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=Decimal("100000"),
                currency=query.currency,
            )
        ]


def _make_config(db_path: Path, **scanner_overrides) -> AppConfig:
    scanner_kwargs = dict(
        provider="mock",
        database_path=db_path,
        currency="CLP",
        days_ahead_start=7,
        days_ahead_end=7,
        stay_lengths=(7,),
        adults=1,
        max_results_per_search=5,
        request_delay_seconds=0,
        scan_interval_minutes=60,
        scan_jitter_seconds=0,
    )
    scanner_kwargs.update(scanner_overrides)
    return AppConfig(
        scanner=ScannerConfig(**scanner_kwargs),
        detection=DetectionConfig(
            discount_ratio=Decimal("0.35"),
            mistake_fare_ratio=Decimal("0.55"),
            min_history_quotes=4,
            history_days=180,
        ),
        notifications=NotificationConfig(
            console=False, webhook_url=None,
            telegram_bot_token=None, telegram_chat_id=None,
        ),
        routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("130000")),),
        origins=(
            Origin(
                code="AEP", name="Buenos Aires", destinations=("SCL",),
                default_max_price=Decimal("250000"),
                default_mistake_fare_below=Decimal("130000"),
            ),
        ),
    )


class DashboardServerTest(unittest.TestCase):
    def test_summary_does_not_expose_demo_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(Path(tmpdir) / "faresnipe-demo.sqlite3")
            server = DashboardServer(config=config)

            self.assertNotIn("is_demo", server.summary())

    def test_summary_and_route_rows_reflect_store_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
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
                routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("130000"), mistake_fare_below=Decimal("70000")),),
                origins=(
                    Origin(
                        code="AEP", name="Buenos Aires", destinations=("SCL",),
                        default_max_price=Decimal("250000"),
                        default_mistake_fare_below=Decimal("130000"),
                    ),
                ),
            )
            store = FareStore(db_path)
            store.save_quote(FareQuote(
                provider="mock", origin="AEP", destination="SCL",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("123826"), currency="CLP", carrier="LATAM",
                stops=0, duration="3 hr 26 min",
            ))
            server = DashboardServer(config=config)
            summary = server.summary()
            rows = server.enrich_rows([dict(row) for row in server.store.latest_by_route()])

            self.assertEqual(summary["routes_seen"], 1)
            self.assertEqual(summary["samples"], 1)
            self.assertEqual(summary["provider_label"], "Mock")
            self.assertFalse(summary["scan_running"])
            self.assertEqual(summary["scan_mode"], "Manual desde dashboard")
            self.assertEqual(summary["watch_interval_minutes"], 60)
            self.assertEqual(rows[0]["origin"], "AEP")
            self.assertEqual(rows[0]["origin_name"], "Buenos Aires")
            self.assertEqual(rows[0]["destination"], "SCL")
            self.assertEqual(rows[0]["carrier"], "LATAM")
            self.assertEqual(rows[0]["status_kind"], "deal")
            self.assertEqual(rows[0]["status_label"], "Buen precio")
            self.assertEqual(rows[0]["quote_type"], "deal")
            self.assertEqual(rows[0]["quote_type_label"], "Deal")

            payload = server.config_payload()
            self.assertEqual(payload["routes"][0]["max_price"], "130000")
            self.assertEqual(payload["origins"][0]["name"], "Buenos Aires")
            self.assertEqual(payload["origins"][0]["route_count"], 1)

    def test_enriched_rows_expose_persisted_mistake_fare_quote_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
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
                routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("130000"), mistake_fare_below=Decimal("70000")),),
                origins=(Origin(code="AEP", name="Buenos Aires", destinations=("SCL",)),),
            )
            quote = FareQuote(
                provider="mock", origin="AEP", destination="SCL",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("50000"), currency="CLP",
            )
            alert = DealAlert(
                quote=quote,
                reasons=("price <= mistake threshold",),
                severity="mistake_fare",
                baseline=Baseline(None, None, 0),
                discount_ratio=None,
            )
            FareStore(db_path).save_quote(quote, alert=alert, baseline=alert.baseline)

            server = DashboardServer(config=config)
            rows = server.enrich_rows([dict(row) for row in server.store.latest_by_route()])

            self.assertEqual(rows[0]["severity"], "mistake_fare")
            self.assertEqual(rows[0]["quote_type"], "mistake_fare")
            self.assertEqual(rows[0]["quote_type_label"], "Mistake fare")

    def test_run_scan_persists_scan_run_for_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = _make_config(db_path)
            server = DashboardServer(config=config)
            result = server.run_scan(limit_searches=1, provider_names=("mock",))
            runs = server.scan_runs_payload()
            summary = server.summary()

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["searches"], 1)
            self.assertEqual(result["failures"], 0)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "success")
            self.assertEqual(runs[0]["searches"], 1)
            self.assertEqual(runs[0]["failures"], 0)
            self.assertEqual(summary["last_scan"]["status"], "success")

    def test_dashboard_filters_rows_to_active_provider_and_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="google_flights_structured",
                    providers=("google_flights_structured",),
                    database_path=db_path, currency="CLP",
                    days_ahead_start=7, days_ahead_end=7, stay_lengths=(7,),
                    adults=1, max_results_per_search=2, request_delay_seconds=0,
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
                routes=(Route(origin="AEP", destination="SCL", max_price=Decimal("130000")),),
                origins=(Origin(code="AEP", name="Buenos Aires", destinations=("SCL",)),),
            )
            store = FareStore(db_path)
            store.save_quote(FareQuote(
                provider="mock", origin="SCL", destination="EZE",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("250"), currency="USD",
            ))
            store.save_quote(FareQuote(
                provider="google_flights_structured", origin="AEP", destination="SCL",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("120000"), currency="CLP",
            ))
            server = DashboardServer(config=config)
            summary = server.summary()
            rows = server.enrich_rows([dict(row) for row in server.store.latest_by_route(
                currency=config.scanner.currency,
                providers=config.scanner.provider_names,
            )])

            self.assertEqual(summary["routes_seen"], 1)
            self.assertEqual(summary["samples"], 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["provider"], "google_flights_structured")
            self.assertEqual(rows[0]["currency"], "CLP")

    def test_configured_routes_payload_includes_unscanned_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="mock", database_path=db_path, currency="CLP",
                    days_ahead_start=7, days_ahead_end=7, stay_lengths=(7,),
                    adults=1, max_results_per_search=2, request_delay_seconds=0,
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
                    Route(origin="SCL", destination="EZE", max_price=Decimal("160000")),
                    Route(origin="SCL", destination="LIM", max_price=Decimal("190000")),
                ),
                origins=(
                    Origin(
                        code="SCL", name="Santiago", destinations=("EZE", "LIM"),
                        default_max_price=Decimal("400000"),
                    ),
                ),
            )
            store = FareStore(db_path)
            store.save_quote(FareQuote(
                provider="mock", origin="SCL", destination="EZE",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("120000"), currency="CLP",
            ))
            server = DashboardServer(config=config)
            rows = server.configured_routes_payload()

            self.assertEqual([f"{row['origin']}-{row['destination']}" for row in rows], ["SCL-EZE", "SCL-LIM"])
            self.assertEqual(rows[0]["origin_name"], "Santiago")
            self.assertTrue(rows[0]["has_price"])
            self.assertFalse(rows[1]["has_price"])
            # Las rutas sin datos van a la sección "Pendientes" (status interno: unscanned).
            self.assertEqual(rows[1]["status_kind"], "unscanned")
            self.assertEqual(rows[1]["status_label"], "Pendiente")

    def test_run_scan_exposes_failure_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = _make_config(db_path, days_ahead_end=8, provider="flaky")
            server = DashboardServer(config=config)

            with patch("faresnipe.dashboard.state.build_providers", return_value=(DashboardFlakyProvider(),)):
                result = server.run_scan(limit_searches=2, provider_names=("flaky",))
            runs = server.scan_runs_payload()
            failures = server.scan_failures_payload(run_id=result["id"])

            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["failures"], 1)
            self.assertEqual(runs[0]["failure_details"][0]["origin"], "AEP")
            self.assertEqual(runs[0]["failure_details"][0]["destination"], "SCL")
            self.assertIn("google blocked route", runs[0]["failure_details"][0]["error"])
            self.assertEqual(failures[0]["scan_run_id"], result["id"])

    def test_run_scan_can_filter_configured_routes_by_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="origin_provider", database_path=db_path, currency="CLP",
                    days_ahead_start=7, days_ahead_end=7, stay_lengths=(7,),
                    adults=1, max_results_per_search=2, request_delay_seconds=0,
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
                    Route(origin="SCL", destination="EZE", max_price=Decimal("160000")),
                    Route(origin="AEP", destination="SCL", max_price=Decimal("130000")),
                ),
                origins=(
                    Origin(code="SCL", name="Santiago", destinations=("EZE",)),
                    Origin(code="AEP", name="Buenos Aires", destinations=("SCL",)),
                ),
            )
            provider = DashboardOriginProvider()
            server = DashboardServer(config=config)

            with patch("faresnipe.dashboard.state.build_providers", return_value=(provider,)):
                result = server.run_scan(limit_searches=10, provider_names=("origin_provider",), origin="AEP")

            self.assertEqual(result["searches"], 1)
            self.assertEqual(provider.origins, ["AEP"])

    def test_http_origins_endpoint_returns_configured_origins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = _make_config(db_path)
            with _running_server(config) as base_url:
                import urllib.request
                with urllib.request.urlopen(f"{base_url}/api/origins") as resp:
                    body = json.loads(resp.read().decode("utf-8"))

            self.assertEqual(body["origins"][0]["code"], "AEP")
            self.assertEqual(body["origins"][0]["name"], "Buenos Aires")
            self.assertEqual(body["origins"][0]["route_count"], 1)

    def test_http_routes_endpoint_includes_origin_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = _make_config(db_path)
            with _running_server(config) as base_url:
                import urllib.request
                with urllib.request.urlopen(f"{base_url}/api/routes") as resp:
                    body = json.loads(resp.read().decode("utf-8"))

            self.assertEqual(body["rows"][0]["origin"], "AEP")
            self.assertEqual(body["rows"][0]["origin_name"], "Buenos Aires")

    def test_http_compare_origins_endpoint_returns_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="mock", database_path=db_path, currency="CLP",
                    days_ahead_start=7, days_ahead_end=7, stay_lengths=(7,),
                    adults=1, max_results_per_search=2, request_delay_seconds=0,
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
            store = FareStore(db_path)
            store.save_quote(FareQuote(
                provider="mock", origin="SCL", destination="EZE",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("120000"), currency="CLP",
            ))
            with _running_server(config) as base_url:
                import urllib.request
                with urllib.request.urlopen(f"{base_url}/api/compare-origins?destination=EZE") as resp:
                    body = json.loads(resp.read().decode("utf-8"))

            self.assertEqual(body["destination"], "EZE")
            self.assertEqual([row["origin"] for row in body["rows"]], ["SCL", "AEP"])
            self.assertEqual(body["rows"][0]["cheapest_price"], "120000")
            self.assertIsNone(body["rows"][1]["cheapest_price"])

    def test_compare_origins_uses_watched_connection_when_direct_quote_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="mock", database_path=db_path, currency="USD",
                    days_ahead_start=7, days_ahead_end=7, stay_lengths=(7,),
                    adults=1, max_results_per_search=2, request_delay_seconds=0,
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
                    Route(origin="AEP", destination="SCL"),
                    Route(origin="SCL", destination="EZE"),
                    Route(origin="AEP", destination="EZE"),
                ),
                origins=(
                    Origin(code="AEP", name="Buenos Aires", destinations=("SCL", "EZE")),
                    Origin(code="SCL", name="Santiago", destinations=("EZE",)),
                ),
            )
            store = FareStore(db_path)
            store.save_quote(FareQuote(
                provider="mock", origin="AEP", destination="SCL",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("233"), currency="USD",
            ))
            store.save_quote(FareQuote(
                provider="mock", origin="SCL", destination="EZE",
                departure_date=date(2026, 8, 20), return_date=date(2026, 8, 27),
                price=Decimal("527"), currency="USD",
            ))

            rows = DashboardServer(config=config).compare_origins_payload("EZE", None, None)
            aep = next(row for row in rows if row["origin"] == "AEP")

            self.assertEqual(aep["cheapest_price"], "760")
            self.assertEqual(aep["cheapest_carrier"], "via SCL")
            self.assertEqual(aep["samples"], 2)
            self.assertEqual(aep["via"], "SCL")

    def test_http_static_and_index_files_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = _make_config(db_path)
            with _running_server(config) as base_url:
                import urllib.request
                with urllib.request.urlopen(f"{base_url}/") as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn(b"faresnipe", resp.read())
                with urllib.request.urlopen(f"{base_url}/static/style.css") as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn(b"--bg", resp.read())
                with urllib.request.urlopen(f"{base_url}/static/app.js") as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn(b"refreshAll", resp.read())

    def test_http_static_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = _make_config(db_path)
            with _running_server(config) as base_url:
                import urllib.error, urllib.request
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"{base_url}/static/../state.py")
                self.assertEqual(ctx.exception.code, 404)

    def test_opportunities_payload_ranks_by_discount_and_enriches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="mock", database_path=db_path, currency="USD",
                    days_ahead_start=7, days_ahead_end=14, stay_lengths=(7,),
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
                routes=(Route(origin="SCL", destination="MIA", max_price=Decimal("700"), mistake_fare_below=Decimal("350")),),
                origins=(Origin(code="SCL", name="Santiago", destinations=("MIA",)),),
            )
            store = FareStore(db_path)
            for i, price in enumerate([500, 520, 540, 560, 580]):
                store.save_quote(FareQuote(
                    provider="mock", origin="SCL", destination="MIA",
                    departure_date=date(2026, 7, 10 + i), return_date=date(2026, 7, 17 + i),
                    price=Decimal(price), currency="USD",
                ))
            store.save_quote(FareQuote(
                provider="mock", origin="SCL", destination="MIA",
                departure_date=date(2026, 7, 20), return_date=date(2026, 7, 27),
                price=Decimal("400"), currency="USD", carrier="LATAM",
                booking_url="https://example.com/mia",
            ))
            server = DashboardServer(config=config)
            rows = server.opportunities_payload()

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["origin"], "SCL")
            self.assertEqual(row["origin_name"], "Santiago")
            self.assertEqual(row["destination"], "MIA")
            self.assertEqual(row["carrier"], "LATAM")
            self.assertEqual(row["booking_url"], "https://example.com/mia")
            self.assertEqual(row["max_price"], "700")
            self.assertEqual(row["mistake_fare_below"], "350")
            self.assertEqual(row["currency"], "USD")
            self.assertIn("bajo mediana", row["opportunity_note"])
            # El descuento (540-400)/540 = 25.9% cae entre 10% y 35% → "opportunity".
            self.assertEqual(row["status_kind"], "opportunity")
            self.assertEqual(row["status_label"], "Oportunidad")

    def test_opportunities_payload_empty_when_no_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "dashboard.sqlite3"
            config = AppConfig(
                scanner=ScannerConfig(
                    provider="mock", database_path=db_path, currency="USD",
                    days_ahead_start=7, days_ahead_end=14, stay_lengths=(7,),
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
                routes=(Route(origin="SCL", destination="BOG"),),
            )
            FareStore(db_path)
            server = DashboardServer(config=config)
            self.assertEqual(server.opportunities_payload(), [])

    def test_classify_row_deterministic_with_magic_score_removed(self) -> None:
        from faresnipe.dashboard.state import _classify_row
        route = Route(origin="AEP", destination="SCL", max_price=Decimal("200000"), mistake_fare_below=Decimal("50000"))

        strong_threshold_row = {"price": "30000", "route_min_price": "100000"}
        self.assertEqual(_classify_row(strong_threshold_row, route)["status_kind"], "deal")

        deal_row = {"price": "150000", "route_min_price": "200000"}
        self.assertEqual(_classify_row(deal_row, route)["status_kind"], "deal")

        normal_row = {"price": "250000", "route_min_price": "200000"}
        self.assertEqual(_classify_row(normal_row, route)["status_kind"], "normal")
        # Sin precio: normal con label explícito
        self.assertEqual(_classify_row({"price": None}, route)["status_kind"], "normal")



if __name__ == "__main__":
    unittest.main()

def _running_server(config: AppConfig):
    """Levanta el dashboard HTTP en un thread y devuelve la URL base."""
    import socket
    import threading
    from contextlib import contextmanager

    from faresnipe.dashboard.server import _build_handler
    from http.server import ThreadingHTTPServer

    @contextmanager
    def _ctx():
        # Buscar un puerto libre
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = DashboardServer(config=config)
        httpd = ThreadingHTTPServer(("127.0.0.1", port), _build_handler(server))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    return _ctx()
