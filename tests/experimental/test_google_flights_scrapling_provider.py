from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from scrapling.parser import Adaptor

from faresnipe.models import SearchQuery
from faresnipe.providers import build_provider
from faresnipe.providers.experimental.google_flights_scrapling import (
    GoogleFlightsScraplingProvider,
    google_flights_url,
)


class FakeFetcher:
    def __init__(self, html: str) -> None:
        self.html = html
        self.urls: list[str] = []

    def fetch(self, url: str):
        self.urls.append(url)
        return Adaptor(self.html, url=url)


class GoogleFlightsScraplingProviderTest(unittest.TestCase):
    def test_build_provider_accepts_scrapling_provider(self) -> None:
        self.assertIsInstance(build_provider("google_flights_scrapling"), GoogleFlightsScraplingProvider)

    def test_builds_google_flights_url(self) -> None:
        query = SearchQuery(
            origin="SCL",
            destination="EZE",
            departure_date=date(2026, 8, 15),
            return_date=date(2026, 8, 22),
            adults=1,
            currency="CLP",
        )

        url = google_flights_url(query)

        self.assertIn("https://www.google.com/travel/flights?", url)
        self.assertIn("Flights+from+SCL+to+EZE+on+2026-08-15+through+2026-08-22", url)
        self.assertIn("curr=CLP", url)

    def test_maps_scrapling_page_to_quotes(self) -> None:
        html = """
        <html><body>
          <div><span data-gs="summary" aria-label="156394 pesos chilenos">CLP 156,394</span></div>
          <ol>
            <li>
              8:30 a.m. 8:30 a.m. del sab, 15 ago - 11:30 a.m.
              11:30 a.m. del sab, 15 ago LATAM Operado por Latam Airlines Group
              2 h SCL Aeropuerto Internacional Arturo Merino Benitez - EZE
              Aeropuerto Internacional Ezeiza Directo
              <span data-gs="la541" aria-label="159470 pesos chilenos" role="text">CLP 159,470</span>
              ida y vuelta
              <span data-gs="la541" aria-label="159470 pesos chilenos" role="text">CLP 159,470</span>
            </li>
            <li>
              10:01 a.m. del sab, 15 ago - 4:50 p.m. del sab, 15 ago
              LATAM, Aerolineas Argentinas 5 h 49 min 1 parada
              <span data-gs="la492-ar1533" aria-label="279797 pesos chilenos" role="text">CLP 279,797</span>
            </li>
          </ol>
        </body></html>
        """
        fetcher = FakeFetcher(html)
        provider = GoogleFlightsScraplingProvider(fetcher=fetcher)
        query = SearchQuery(
            origin="SCL",
            destination="EZE",
            departure_date=date(2026, 8, 15),
            return_date=date(2026, 8, 22),
            adults=1,
            currency="CLP",
        )

        quotes = provider.search(query, max_results=5)

        self.assertEqual(len(quotes), 2)
        self.assertEqual(quotes[0].provider, "google_flights_scrapling")
        self.assertEqual(quotes[0].price, Decimal("159470"))
        self.assertEqual(quotes[0].currency, "CLP")
        self.assertEqual(quotes[0].carrier, "LATAM")
        self.assertEqual(quotes[0].stops, 0)
        self.assertEqual(quotes[0].duration, "2 h")
        self.assertEqual(quotes[0].departure_time, "08:30")
        self.assertEqual(quotes[0].arrival_time, "11:30")
        self.assertEqual(quotes[0].raw["source"], "google_flights_scrapling")
        self.assertEqual(quotes[1].price, Decimal("279797"))
        self.assertEqual(quotes[1].stops, 1)
        self.assertEqual(len(fetcher.urls), 1)


if __name__ == "__main__":
    unittest.main()
