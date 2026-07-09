from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from faresnipe.models import SearchQuery
from faresnipe.providers.experimental.flight_finder_scraper import (
    FlightFinderScraperProvider,
    ScrapedPage,
    build_google_flights_url_candidates,
    page_has_requested_route,
    parse_google_flights_text,
)


class StaticFetcher:
    def __init__(self, text: str) -> None:
        self.text = text
        self.urls = []

    def fetch_text(self, url: str) -> ScrapedPage:
        self.urls.append(url)
        return ScrapedPage(text=self.text, final_url=url)


class FlightFinderScraperProviderTest(unittest.TestCase):
    def test_builds_google_flights_url_candidates(self) -> None:
        query = SearchQuery(
            origin="SCL",
            destination="MAD",
            departure_date=date(2026, 9, 1),
            return_date=date(2026, 9, 15),
            adults=1,
            currency="USD",
        )

        urls = build_google_flights_url_candidates(query)

        self.assertEqual(len(urls), 3)
        self.assertTrue(all("google.com/travel/flights" in url for url in urls))
        self.assertTrue(all("curr=USD" in url for url in urls))
        self.assertTrue(all("gl=CL" in url for url in urls))

    def test_parse_google_flights_text_extracts_sorted_unique_prices(self) -> None:
        query = SearchQuery(
            origin="SCL",
            destination="MAD",
            departure_date=date(2026, 9, 1),
            return_date=date(2026, 9, 15),
            adults=1,
            currency="USD",
        )
        text = """
        Flights from SCL to MAD
        Iberia 1 stop USD 611
        LATAM nonstop $589
        Another repeated option $589
        Premium cabin USD 4,200
        """

        quotes = parse_google_flights_text(text, query, "https://example.test", max_results=3)

        self.assertEqual([quote.price for quote in quotes], [Decimal("589"), Decimal("611"), Decimal("4200")])
        self.assertEqual(quotes[0].provider, "flight_finder_scraper")
        self.assertEqual(quotes[0].booking_url, "https://example.test")

    def test_parse_google_flights_text_handles_clp_grouping(self) -> None:
        query = SearchQuery(
            origin="AEP",
            destination="SCL",
            departure_date=date(2026, 9, 1),
            return_date=date(2026, 9, 8),
            adults=1,
            currency="CLP",
        )
        text = """
        Vuelos desde AEP a SCL
        6:00 AM 8:20 AM Sky Airline 3 hr 20 min AEP?SCL Nonstop CLP?98,500
        9:00 AM 11:20 AM LATAM 3 hr 20 min AEP?SCL Nonstop CLP 120.000
        """

        quotes = parse_google_flights_text(text, query, "https://example.test", max_results=2)

        self.assertEqual([quote.price for quote in quotes], [Decimal("98500"), Decimal("120000")])

    def test_parse_google_flights_text_ignores_price_insight_delta(self) -> None:
        query = SearchQuery(
            origin="AEP",
            destination="SCL",
            departure_date=date(2026, 8, 20),
            return_date=date(2026, 8, 27),
            adults=1,
            currency="CLP",
        )
        text = """
        Flights from AEP to SCL
        Cheapest from CLP?122,678
        Prices are currently low ? CLP?28,550 cheaper than usual for your search
        LATAM AEP?SCL Nonstop CLP?123,826 round trip
        """

        quotes = parse_google_flights_text(text, query, "https://example.test", max_results=5)

        self.assertEqual([quote.price for quote in quotes], [Decimal("123826")])

    def test_parse_google_flights_text_extracts_details_from_block(self) -> None:
        query = SearchQuery(
            origin="AEP",
            destination="SCL",
            departure_date=date(2026, 8, 20),
            return_date=date(2026, 8, 27),
            adults=1,
            currency="CLP",
        )
        text = """
        4:18 AM ??? 6:44 AM
        LATAM Operated by Latam Airlines Group
        3 hr 26 min
        AEP?SCL
        Nonstop
        CLP?123,826
        round trip
        """

        quotes = parse_google_flights_text(text, query, "https://example.test", max_results=5)

        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].carrier, "LATAM")
        self.assertEqual(quotes[0].stops, 0)
        self.assertEqual(quotes[0].duration, "3 hr 26 min")
        self.assertEqual(quotes[0].departure_time, "4:18 AM")
        self.assertEqual(quotes[0].arrival_time, "6:44 AM")

    def test_provider_uses_first_candidate_with_requested_route(self) -> None:
        query = SearchQuery(
            origin="SCL",
            destination="MAD",
            departure_date=date(2026, 9, 1),
            return_date=date(2026, 9, 15),
            adults=1,
            currency="USD",
        )
        fetcher = StaticFetcher(
            "Flights from SCL to MAD 10:00 AM 6:00 AM Iberia 13 hr 20 min "
            "SCL?MAD Nonstop USD 399"
        )
        provider = FlightFinderScraperProvider(fetcher=fetcher, settle_seconds=0)

        quotes = provider.search(query, max_results=2)

        self.assertEqual(len(fetcher.urls), 1)
        self.assertEqual([quote.price for quote in quotes], [Decimal("399")])

    def test_page_has_requested_route_supports_english_and_spanish(self) -> None:
        self.assertTrue(page_has_requested_route("Flights from SCL to MAD", "SCL", "MAD"))
        self.assertTrue(page_has_requested_route("Vuelos desde AEP a SCL", "AEP", "SCL"))
        self.assertFalse(page_has_requested_route("Flights from MAD to SCL", "SCL", "MAD"))


if __name__ == "__main__":
    unittest.main()
