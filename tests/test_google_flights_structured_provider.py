from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from faresnipe.models import FareQuote, SearchQuery
from faresnipe.providers import build_provider
from faresnipe.providers.google_flights_structured import (
    GoogleFlightsStructuredError,
    GoogleFlightsStructuredProvider,
    google_flights_booking_url,
)


@dataclass
class FakeClock:
    time: tuple[int | None, int | None]


@dataclass
class FakeAirport:
    code: str
    name: str


@dataclass
class FakeSegment:
    from_airport: FakeAirport
    to_airport: FakeAirport
    departure: FakeClock
    arrival: FakeClock
    duration: int
    plane_type: str = "A320"


@dataclass
class FakeFlight:
    type: str
    price: int
    airlines: list[str]
    flights: list[FakeSegment]
    carbon: None = None


class FakeFallback:
    name = "fallback"

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        return [
            FareQuote(
                provider=self.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=Decimal("99000"),
                currency=query.currency,
            )
        ]


class GoogleFlightsStructuredProviderTest(unittest.TestCase):
    def test_maps_structured_fast_flights_results_to_quotes(self) -> None:
        query = SearchQuery(
            origin="AEP",
            destination="SCL",
            departure_date=date(2026, 9, 15),
            return_date=None,
            adults=1,
            currency="CLP",
        )
        result = [
            FakeFlight(
                type="LA",
                price=81914,
                airlines=["LATAM"],
                flights=[
                    FakeSegment(
                        from_airport=FakeAirport("AEP", "Buenos Aires"),
                        to_airport=FakeAirport("SCL", "Santiago"),
                        departure=FakeClock((1, 42)),
                        arrival=FakeClock((4, 8)),
                        duration=146,
                    )
                ],
            )
        ]
        provider = GoogleFlightsStructuredProvider(get_flights_func=lambda q: result, enable_fallback=False)

        quotes = provider.search(query, max_results=5)

        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].provider, "google_flights_structured")
        self.assertEqual(quotes[0].price, Decimal("81914"))
        self.assertEqual(quotes[0].currency, "CLP")
        self.assertEqual(quotes[0].carrier, "LATAM")
        self.assertEqual(quotes[0].stops, 0)
        self.assertEqual(quotes[0].duration, "2 hr 26 min")
        self.assertEqual(quotes[0].departure_time, "01:42")
        self.assertEqual(quotes[0].arrival_time, "04:08")
        self.assertEqual(
            quotes[0].booking_url,
            "https://www.google.com/travel/flights?q=Flights+from+AEP+to+SCL+on+2026-09-15",
        )
        self.assertEqual(quotes[0].raw["source"], "fast_flights_structured")

    def test_falls_back_when_structured_provider_fails(self) -> None:
        query = SearchQuery(
            origin="SCL",
            destination="LIM",
            departure_date=date(2026, 9, 15),
            return_date=date(2026, 9, 22),
            adults=1,
            currency="CLP",
        )

        provider = GoogleFlightsStructuredProvider(
            get_flights_func=lambda q: (_ for _ in ()).throw(IndexError("list index out of range")),
            fallback=FakeFallback(),
        )

        quotes = provider.search(query, max_results=5)

        self.assertEqual(quotes[0].provider, "fallback")
        self.assertEqual(quotes[0].price, Decimal("99000"))

    def test_formats_missing_midnight_hour_from_fast_flights(self) -> None:
        query = SearchQuery(
            origin="JFK",
            destination="LAX",
            departure_date=date(2026, 9, 15),
            return_date=None,
            adults=1,
            currency="USD",
        )
        result = [
            FakeFlight(
                type="B6",
                price=154,
                airlines=["JetBlue"],
                flights=[
                    FakeSegment(
                        from_airport=FakeAirport("JFK", "New York"),
                        to_airport=FakeAirport("LAX", "Los Angeles"),
                        departure=FakeClock((None, 55)),
                        arrival=FakeClock((8, 57)),
                        duration=357,
                    )
                ],
            )
        ]
        provider = GoogleFlightsStructuredProvider(get_flights_func=lambda q: result, enable_fallback=False)

        quotes = provider.search(query, max_results=5)

        self.assertEqual(quotes[0].departure_time, "00:55")

    def test_raises_clear_error_without_fallback(self) -> None:
        query = SearchQuery(
            origin="SCL",
            destination="LIM",
            departure_date=date(2026, 9, 15),
            return_date=None,
            adults=1,
            currency="CLP",
        )
        provider = GoogleFlightsStructuredProvider(
            get_flights_func=lambda q: (_ for _ in ()).throw(RuntimeError("blocked")),
            enable_fallback=False,
        )

        with self.assertRaises(GoogleFlightsStructuredError):
            provider.search(query, max_results=5)

    def test_build_provider_accepts_no_key_aliases(self) -> None:
        self.assertEqual(build_provider("google_flights_structured").name, "google_flights_structured")
        self.assertEqual(build_provider("fast_flights").name, "google_flights_structured")

    def test_builds_round_trip_booking_url_with_executed_search_query(self) -> None:
        query = SearchQuery(
            origin="scl",
            destination="eze",
            departure_date=date(2026, 8, 19),
            return_date=date(2026, 8, 26),
            adults=2,
            currency="usd",
        )

        url = google_flights_booking_url(query)

        self.assertEqual(
            url,
            "https://www.google.com/travel/flights?q=Flights+from+SCL+to+EZE+on+2026-08-19+through+2026-08-26",
        )

    def test_builds_one_way_booking_url_with_executed_search_query(self) -> None:
        query = SearchQuery(
            origin="scl",
            destination="eze",
            departure_date=date(2026, 8, 19),
            return_date=None,
            adults=2,
            currency="usd",
        )

        url = google_flights_booking_url(query)

        self.assertEqual(
            url,
            "https://www.google.com/travel/flights?q=Flights+from+SCL+to+EZE+on+2026-08-19",
        )


if __name__ == "__main__":
    unittest.main()
