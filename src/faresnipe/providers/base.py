from __future__ import annotations

from typing import Protocol

from faresnipe.models import FareQuote, SearchQuery


class FlightProvider(Protocol):
    name: str

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        ...
