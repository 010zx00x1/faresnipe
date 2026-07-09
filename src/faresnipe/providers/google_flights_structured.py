from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote_plus
from typing import Any

from faresnipe.models import FareQuote, SearchQuery


class GoogleFlightsStructuredError(RuntimeError):
    pass


class GoogleFlightsStructuredProvider:
    name = "google_flights_structured"

    def __init__(
        self,
        get_flights_func: Callable[[Any], Any] | None = None,
        fallback: object | None = None,
        language: str | None = None,
        enable_fallback: bool | None = None,
    ) -> None:
        self.get_flights_func = get_flights_func
        self.language = language or os.environ.get("FARESNIPE_GOOGLE_FLIGHTS_LANGUAGE", "es-419")
        if enable_fallback is None:
            enable_fallback = os.environ.get("FARESNIPE_GOOGLE_FLIGHTS_FALLBACK", "1") not in {"0", "false", "False"}
        self.fallback = fallback

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        try:
            result = self._get_flights(query)
            quotes = self._quotes_from_result(result, query, max_results)
            if quotes:
                return quotes
            error: Exception | None = GoogleFlightsStructuredError("Google Flights returned no structured fares.")
        except Exception as exc:
            error = exc

        if self.fallback is not None:
            try:
                return self.fallback.search(query, max_results)
            except Exception as fallback_exc:
                raise GoogleFlightsStructuredError(
                    f"Structured Google Flights failed: {error}; fallback failed: {fallback_exc}"
                ) from fallback_exc

        raise GoogleFlightsStructuredError(f"Structured Google Flights failed: {error}") from error

    def _get_flights(self, query: SearchQuery) -> Any:
        try:
            from fast_flights import FlightQuery, Passengers, create_query, get_flights
        except ImportError as exc:
            raise GoogleFlightsStructuredError(
                "fast-flights is required for provider='google_flights_structured'. "
                "Install dependencies with: python3 -m pip install -e ."
            ) from exc

        flights = [
            FlightQuery(
                date=query.departure_date.isoformat(),
                from_airport=query.origin,
                to_airport=query.destination,
            )
        ]
        trip = "one-way"
        if query.return_date is not None:
            flights.append(
                FlightQuery(
                    date=query.return_date.isoformat(),
                    from_airport=query.destination,
                    to_airport=query.origin,
                )
            )
            trip = "round-trip"

        fast_query = create_query(
            flights=flights,
            trip=trip,
            passengers=Passengers(adults=query.adults),
            language=self.language,
            currency=query.currency,
        )
        fetch = self.get_flights_func or get_flights
        return fetch(fast_query)

    def _quotes_from_result(self, result: Any, query: SearchQuery, max_results: int) -> list[FareQuote]:
        quotes: list[FareQuote] = []
        seen: set[tuple[Decimal, tuple[str, ...], str | None]] = set()
        for item in list(result)[: max(max_results * 3, max_results)]:
            quote = _quote_from_fast_flight(item, query)
            if quote is None:
                continue
            key = (quote.price, quote.flight_numbers, quote.departure_time)
            if key in seen:
                continue
            seen.add(key)
            quotes.append(quote)
            if len(quotes) >= max_results:
                break
        quotes.sort(key=lambda quote: quote.price)
        return quotes


def _quote_from_fast_flight(item: Any, query: SearchQuery) -> FareQuote | None:
    price = _decimal_or_none(getattr(item, "price", None))
    segments = list(getattr(item, "flights", []) or [])
    if price is None or not segments:
        return None

    first = segments[0]
    last = segments[-1]
    airlines = tuple(str(value) for value in (getattr(item, "airlines", []) or []) if value)
    carrier = ", ".join(airlines) or str(getattr(item, "type", "") or "") or None
    flight_numbers = tuple(_segment_flight_number(segment) for segment in segments)
    flight_numbers = tuple(value for value in flight_numbers if value)
    duration_minutes = sum(int(getattr(segment, "duration", 0) or 0) for segment in segments)
    stops = max(0, len(segments) - 1)

    return FareQuote(
        provider=GoogleFlightsStructuredProvider.name,
        origin=query.origin,
        destination=query.destination,
        departure_date=query.departure_date,
        return_date=query.return_date,
        price=price,
        currency=query.currency,
        carrier=carrier,
        flight_numbers=flight_numbers,
        stops=stops,
        duration=_format_duration(duration_minutes),
        departure_time=_format_time(getattr(getattr(first, "departure", None), "time", None)),
        arrival_time=_format_time(getattr(getattr(last, "arrival", None), "time", None)),
        booking_url=google_flights_booking_url(query),
        observed_at=datetime.now(timezone.utc),
        raw={
            "source": "fast_flights_structured",
            "type": getattr(item, "type", None),
            "airlines": list(airlines),
            "segments": [_dataclass_dict(segment) for segment in segments],
            "carbon": _dataclass_dict(getattr(item, "carbon", None)),
        },
    )


def _segment_flight_number(segment: Any) -> str:
    airline = str(getattr(segment, "airline", "") or "").strip()
    number = str(getattr(segment, "flight_number", "") or "").strip()
    return f"{airline}{number}" if airline or number else ""


def _format_duration(minutes: int) -> str | None:
    if minutes <= 0:
        return None
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} hr {mins} min"
    if hours:
        return f"{hours} hr"
    return f"{mins} min"


def _format_time(value: Any) -> str | None:
    if not value:
        return None
    parts = list(value)
    if len(parts) < 2 or parts[1] is None:
        return None
    hour = 0 if parts[0] is None else int(parts[0])
    return f"{hour:02d}:{int(parts[1]):02d}"


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _dataclass_dict(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    return value


def google_flights_booking_url(query: SearchQuery) -> str:
    origin = query.origin.upper()
    destination = query.destination.upper()
    departure = query.departure_date.isoformat()
    search = f"Flights from {origin} to {destination} on {departure}"
    if query.return_date is not None:
        search += f" through {query.return_date.isoformat()}"
    return f"https://www.google.com/travel/flights?q={quote_plus(search)}"


def _google_flights_booking_url(query: SearchQuery) -> str:
    return google_flights_booking_url(query)


def search(
    origin: str,
    destination: str,
    depart_range,
    return_range,
    stay_lengths: tuple[int, ...],
    adults: int,
    currency: str,
) -> list[FareQuote]:
    query = SearchQuery(
        origin=origin,
        destination=destination,
        departure_date=depart_range,
        return_date=return_range,
        adults=adults,
        currency=currency,
    )
    return GoogleFlightsStructuredProvider(enable_fallback=False).search(query, 8)
