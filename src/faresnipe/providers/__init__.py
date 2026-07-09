from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from faresnipe.models import FareQuote, SearchQuery
from faresnipe.providers import google_flights_structured, mock, skyscanner
from faresnipe.providers.base import FlightProvider
from faresnipe.providers.experimental.flight_finder_scraper import FlightFinderScraperProvider
from faresnipe.providers.experimental.google_flights_scrapling import GoogleFlightsScraplingProvider

ProviderSearch = Callable[
    [str, str, date, date | None, tuple[int, ...], int, str],
    list[FareQuote],
]

PROVIDERS: dict[str, ProviderSearch] = {
    "google_flights_structured": google_flights_structured.search,
    "mock": mock.search,
    "skyscanner": skyscanner.search,
}
CLASS_PROVIDERS: dict[str, type[FlightProvider]] = {
    "flight_finder_scraper": FlightFinderScraperProvider,
    "google_flights_scrapling": GoogleFlightsScraplingProvider,
}
ALIASES = {
    "google_flights": "google_flights_structured",
    "fast_flights": "google_flights_structured",
}


@dataclass(frozen=True)
class Provider:
    name: str
    func: ProviderSearch

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        stay_lengths: tuple[int, ...] = ()
        if query.return_date is not None:
            stay_lengths = ((query.return_date - query.departure_date).days,)
        return self.func(
            query.origin,
            query.destination,
            query.departure_date,
            query.return_date,
            stay_lengths,
            query.adults,
            query.currency,
        )[:max_results]


def build_provider(name: str) -> FlightProvider:
    key = ALIASES.get(name.strip().lower(), name.strip().lower())
    if key in CLASS_PROVIDERS:
        return CLASS_PROVIDERS[key]()
    try:
        return Provider(key, PROVIDERS[key])
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {name}") from exc


def build_providers(names: list[str] | tuple[str, ...]) -> tuple[FlightProvider, ...]:
    return tuple(build_provider(name) for name in names)


__all__ = ["PROVIDERS", "Provider", "build_provider", "build_providers"]
