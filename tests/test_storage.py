from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from faresnipe.models import FareQuote
from faresnipe.models import SearchQuery
from faresnipe.storage import FareStore


class FareStoreBaselineTest(unittest.TestCase):
    def test_baseline_uses_comparable_month_stay_and_advance_purchase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FareStore(Path(tmpdir) / "faresnipe.sqlite3")

            comparable = FareQuote(
                provider="test",
                origin="SCL",
                destination="MIA",
                departure_date=date(2026, 9, 10),
                return_date=date(2026, 9, 17),
                price=Decimal("500"),
                currency="USD",
                observed_at=datetime(2026, 7, 10, 12, 0, 0),
            )
            different_month = FareQuote(
                provider="test",
                origin="SCL",
                destination="MIA",
                departure_date=date(2026, 10, 10),
                return_date=date(2026, 10, 17),
                price=Decimal("900"),
                currency="USD",
                observed_at=datetime(2026, 8, 10, 12, 0, 0),
            )
            different_stay = FareQuote(
                provider="test",
                origin="SCL",
                destination="MIA",
                departure_date=date(2026, 9, 10),
                return_date=date(2026, 9, 24),
                price=Decimal("1000"),
                currency="USD",
                observed_at=datetime(2026, 7, 10, 12, 0, 0),
            )
            different_advance = FareQuote(
                provider="test",
                origin="SCL",
                destination="MIA",
                departure_date=date(2026, 9, 10),
                return_date=date(2026, 9, 17),
                price=Decimal("1100"),
                currency="USD",
                observed_at=datetime(2026, 9, 1, 12, 0, 0),
            )
            for quote in (comparable, different_month, different_stay, different_advance):
                store.save_quote(quote)

            current = FareQuote(
                provider="test",
                origin="SCL",
                destination="MIA",
                departure_date=date(2026, 9, 12),
                return_date=date(2026, 9, 19),
                price=Decimal("300"),
                currency="USD",
                observed_at=datetime(2026, 7, 12, 12, 0, 0),
            )

            baseline = store.baseline_for(current, history_days=365)

            self.assertEqual(baseline.quote_count, 1)
            self.assertEqual(baseline.median_price, Decimal("500"))
            self.assertEqual(baseline.min_price, Decimal("500"))

    def test_scan_run_lifecycle_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FareStore(Path(tmpdir) / "faresnipe.sqlite3")
            query = SearchQuery(
                origin="SCL",
                destination="MIA",
                departure_date=date(2026, 9, 10),
                return_date=date(2026, 9, 17),
                adults=1,
                currency="USD",
            )

            run_id = store.begin_scan_run("flight_finder_scraper", limit_searches=3, total_searches=10)
            store.update_scan_run(run_id, searches=1, quotes=4, alerts=0, failures=1, current_query=query)
            store.save_scan_failure(run_id, query, "provider timeout")
            running = store.latest_scan_run()
            failures = store.scan_failures(run_id)

            self.assertIsNotNone(running)
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["searches"], 1)
            self.assertEqual(running["failures"], 1)
            self.assertEqual(running["current_origin"], "SCL")
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["origin"], "SCL")
            self.assertEqual(failures[0]["destination"], "MIA")
            self.assertEqual(failures[0]["error"], "provider timeout")

            store.update_scan_run(run_id, status="partial", searches=3, quotes=9, alerts=1, failures=1, complete=True, clear_current=True)
            finished = store.latest_scan_run()

            self.assertEqual(finished["status"], "partial")
            self.assertEqual(finished["quotes"], 9)
            self.assertEqual(finished["alerts"], 1)
            self.assertEqual(finished["failures"], 1)
            self.assertIsNone(finished["current_origin"])
            self.assertIsNotNone(finished["completed_at"])


class FareStoreTopOpportunitiesTest(unittest.TestCase):
    def _quote(
        self,
        price: str,
        origin: str = "SCL",
        destination: str = "MIA",
        departure: date | None = None,
        return_date: date | None = None,
        observed_at: datetime | None = None,
        currency: str = "USD",
    ) -> FareQuote:
        return FareQuote(
            provider="test",
            origin=origin,
            destination=destination,
            departure_date=departure or date(2026, 7, 20),
            return_date=return_date or date(2026, 7, 27),
            price=Decimal(price),
            currency=currency,
            observed_at=observed_at or datetime(2026, 7, 5, 12, 0, 0),
        )

    def test_top_opportunities_returns_empty_when_no_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FareStore(Path(tmpdir) / "faresnipe.sqlite3")
            self.assertEqual(store.top_opportunities(limit=10, min_discount=Decimal("0.10"), min_history=3, history_days=180), [])

    def test_top_opportunities_filters_below_min_discount(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FareStore(Path(tmpdir) / "faresnipe.sqlite3")
            for i, price in enumerate([500, 520, 540, 560, 580]):
                store.save_quote(self._quote(
                    str(price),
                    departure=date(2026, 7, 10 + i),
                    return_date=date(2026, 7, 17 + i),
                ))
            # Current observation: 510 vs median 540 -> 5.5% off, should be filtered out
            store.save_quote(self._quote("510", departure=date(2026, 7, 20), return_date=date(2026, 7, 27)))
            rows = store.top_opportunities(limit=10, min_discount=Decimal("0.10"), min_history=3, history_days=180)
            self.assertEqual(rows, [])

    def test_top_opportunities_ranks_by_discount_desc(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FareStore(Path(tmpdir) / "faresnipe.sqlite3")
            # MIA: history at 700..780, median 740; current 400 -> 46% off
            for i, price in enumerate([700, 720, 740, 760, 780]):
                store.save_quote(self._quote(
                    str(price),
                    origin="SCL", destination="MIA",
                    departure=date(2026, 7, 10 + i),
                    return_date=date(2026, 7, 17 + i),
                ))
            store.save_quote(self._quote("400", origin="SCL", destination="MIA", departure=date(2026, 7, 20), return_date=date(2026, 7, 27)))
            # BOG: history at 800..880, median 840; current 600 -> 28.6% off
            for i, price in enumerate([800, 820, 840, 860, 880]):
                store.save_quote(self._quote(
                    str(price),
                    origin="SCL", destination="BOG",
                    departure=date(2026, 7, 10 + i),
                    return_date=date(2026, 7, 17 + i),
                ))
            store.save_quote(self._quote("600", origin="SCL", destination="BOG", departure=date(2026, 7, 20), return_date=date(2026, 7, 27)))
            rows = store.top_opportunities(limit=10, min_discount=Decimal("0.10"), min_history=3, history_days=180)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["origin"], "SCL")
            self.assertEqual(rows[0]["destination"], "MIA")
            self.assertEqual(rows[1]["destination"], "BOG")
            self.assertGreater(Decimal(rows[0]["discount_ratio"]), Decimal(rows[1]["discount_ratio"]))
            # baseline includes the current observation itself: 5 historical + 1 current
            self.assertEqual(rows[0]["baseline_count"], 6)
            self.assertEqual(rows[1]["baseline_count"], 6)

    def test_top_opportunities_respects_currency_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FareStore(Path(tmpdir) / "faresnipe.sqlite3")
            for i, price in enumerate([500, 520, 540, 560, 580]):
                store.save_quote(self._quote(
                    str(price), currency="USD",
                    departure=date(2026, 7, 10 + i),
                    return_date=date(2026, 7, 17 + i),
                ))
                store.save_quote(self._quote(
                    str(int(price) * 1000), currency="CLP",
                    departure=date(2026, 7, 10 + i),
                    return_date=date(2026, 7, 17 + i),
                ))
            store.save_quote(self._quote("360", currency="USD", departure=date(2026, 7, 20), return_date=date(2026, 7, 27)))
            store.save_quote(self._quote("360000", currency="CLP", departure=date(2026, 7, 20), return_date=date(2026, 7, 27)))
            clp_rows = store.top_opportunities(
                limit=10, min_discount=Decimal("0.10"), min_history=3, history_days=180,
                currency="CLP",
            )
            self.assertEqual(len(clp_rows), 1)
            self.assertEqual(clp_rows[0]["currency"], "CLP")


if __name__ == "__main__":
    unittest.main()
