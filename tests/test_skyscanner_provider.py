from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from faresnipe.models import SearchQuery
from faresnipe.providers.skyscanner import SkyscannerFlightProvider


class TestableSkyscannerProvider(SkyscannerFlightProvider):
    def __init__(self, responses):  # type: ignore[no-untyped-def]
        super().__init__(api_key="test-key", poll_attempts=0, poll_delay_seconds=0)
        self.responses = responses
        self.paths = []
        self.bodies = []

    def _request_json(self, path, body):  # type: ignore[no-untyped-def]
        self.paths.append(path)
        self.bodies.append(body)
        return self.responses.pop(0)


class SkyscannerProviderTest(unittest.TestCase):
    def test_search_builds_round_trip_query_and_parses_prices(self) -> None:
        provider = TestableSkyscannerProvider(
            [
                {
                    "content": {
                        "results": {
                            "carriers": {"ba": {"name": "British Airways"}},
                            "itineraries": {
                                "it-1": {
                                    "carrierIds": ["ba"],
                                    "pricingOptions": [
                                        {
                                            "price": {"amount": "399.90"},
                                            "items": [{"deepLink": "https://example.test/book"}],
                                        }
                                    ],
                                }
                            },
                        }
                    }
                }
            ]
        )
        query = SearchQuery(
            origin="SCL",
            destination="MAD",
            departure_date=date(2026, 9, 1),
            return_date=date(2026, 9, 15),
            adults=1,
            currency="USD",
        )

        quotes = provider.search(query, max_results=3)

        self.assertEqual(provider.paths, ["/apiservices/v3/flights/live/search/create"])
        request_query = provider.bodies[0]["query"]
        self.assertEqual(request_query["market"], "CL")
        self.assertEqual(request_query["locale"], "es-CL")
        self.assertEqual(len(request_query["queryLegs"]), 2)
        self.assertEqual(quotes[0].price, Decimal("399.90"))
        self.assertEqual(quotes[0].carrier, "British Airways")
        self.assertEqual(quotes[0].booking_url, "https://example.test/book")


if __name__ == "__main__":
    unittest.main()

