from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from faresnipe.config import DetectionConfig
from faresnipe.detector import DealDetector
from faresnipe.models import Baseline, FareQuote, Route


class DealDetectorTest(unittest.TestCase):
    def test_route_threshold_triggers_deal(self) -> None:
        detector = DealDetector(
            DetectionConfig(
                discount_ratio=Decimal("0.35"),
                mistake_fare_ratio=Decimal("0.55"),
                min_history_quotes=4,
                history_days=180,
            )
        )
        quote = FareQuote(
            provider="mock",
            origin="SCL",
            destination="MIA",
            departure_date=date(2026, 9, 1),
            return_date=date(2026, 9, 8),
            price=Decimal("499"),
            currency="USD",
        )
        alert = detector.evaluate(
            quote,
            Route(origin="SCL", destination="MIA", max_price=Decimal("520")),
            Baseline(median_price=None, min_price=None, quote_count=0),
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert.severity, "deal")

    def test_historical_drop_triggers_mistake_fare(self) -> None:
        detector = DealDetector(
            DetectionConfig(
                discount_ratio=Decimal("0.35"),
                mistake_fare_ratio=Decimal("0.55"),
                min_history_quotes=4,
                history_days=180,
            )
        )
        quote = FareQuote(
            provider="mock",
            origin="SCL",
            destination="MAD",
            departure_date=date(2026, 10, 1),
            return_date=date(2026, 10, 15),
            price=Decimal("350"),
            currency="USD",
        )
        alert = detector.evaluate(
            quote,
            Route(origin="SCL", destination="MAD"),
            Baseline(median_price=Decimal("900"), min_price=Decimal("780"), quote_count=12),
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert.severity, "mistake_fare")
        self.assertEqual(alert.discount_ratio, Decimal("0.6111111111111111111111111111"))

    def test_absolute_mistake_threshold_without_large_discount_stays_deal(self) -> None:
        detector = DealDetector(
            DetectionConfig(
                discount_ratio=Decimal("0.35"),
                mistake_fare_ratio=Decimal("0.55"),
                min_history_quotes=4,
                history_days=180,
            )
        )
        quote = FareQuote(
            provider="mock",
            origin="SCL",
            destination="EZE",
            departure_date=date(2026, 8, 1),
            return_date=date(2026, 8, 8),
            price=Decimal("250"),
            currency="USD",
        )
        alert = detector.evaluate(
            quote,
            Route(origin="SCL", destination="EZE", max_price=Decimal("400"), mistake_fare_below=Decimal("260")),
            Baseline(median_price=Decimal("390"), min_price=Decimal("240"), quote_count=12),
        )
        self.assertIsNotNone(alert)
        self.assertEqual(alert.severity, "deal")


if __name__ == "__main__":
    unittest.main()
