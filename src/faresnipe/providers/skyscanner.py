from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from faresnipe.models import FareQuote, SearchQuery


class SkyscannerError(RuntimeError):
    pass


class SkyscannerFlightProvider:
    name = "skyscanner"
    base_url = "https://partners.api.skyscanner.net"

    def __init__(
        self,
        api_key: str | None = None,
        market: str | None = None,
        locale: str | None = None,
        poll_attempts: int = 4,
        poll_delay_seconds: float = 1.5,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_key = api_key or os.environ.get("SKYSCANNER_API_KEY", "")
        self.market = market or os.environ.get("SKYSCANNER_MARKET", "CL")
        self.locale = locale or os.environ.get("SKYSCANNER_LOCALE", "es-CL")
        self.poll_attempts = poll_attempts
        self.poll_delay_seconds = poll_delay_seconds
        self.timeout_seconds = timeout_seconds

        if not self.api_key:
            raise SkyscannerError(
                "Missing SKYSCANNER_API_KEY. Skyscanner is free to use only after partner approval; "
                "use provider='mock' until you have an approved key."
            )

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        body = {"query": self._query_payload(query)}
        response = self._request_json(
            "/apiservices/v3/flights/live/search/create",
            body,
        )
        quotes = self._parse_quotes(query, response)

        session_token = response.get("sessionToken")
        for _ in range(self.poll_attempts):
            if len(quotes) >= max_results or not session_token:
                break
            time.sleep(self.poll_delay_seconds)
            response = self._request_json(
                f"/apiservices/v3/flights/live/search/poll/{session_token}",
                None,
            )
            quotes.extend(self._parse_quotes(query, response))

        unique = self._dedupe(quotes)
        unique.sort(key=lambda quote: quote.price)
        return unique[:max_results]

    def _query_payload(self, query: SearchQuery) -> dict[str, Any]:
        legs = [
            {
                "originPlaceId": {"iata": query.origin},
                "destinationPlaceId": {"iata": query.destination},
                "date": _date_payload(query.departure_date),
            }
        ]
        if query.return_date:
            legs.append(
                {
                    "originPlaceId": {"iata": query.destination},
                    "destinationPlaceId": {"iata": query.origin},
                    "date": _date_payload(query.return_date),
                }
            )

        return {
            "market": self.market,
            "locale": self.locale,
            "currency": query.currency,
            "queryLegs": legs,
            "adults": query.adults,
            "cabinClass": "CABIN_CLASS_ECONOMY",
        }

    def _request_json(self, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise SkyscannerError(f"Skyscanner API error {exc.code}: {details}") from exc

    def _parse_quotes(self, query: SearchQuery, response: dict[str, Any]) -> list[FareQuote]:
        content = response.get("content", {})
        results = content.get("results", {})
        itineraries = results.get("itineraries", {})
        carriers = results.get("carriers", {})
        quotes: list[FareQuote] = []

        for itinerary in _values(itineraries):
            pricing_options = itinerary.get("pricingOptions") or []
            for option in pricing_options:
                price = _extract_price(option)
                if price is None:
                    continue
                quotes.append(
                    FareQuote(
                        provider=self.name,
                        origin=query.origin,
                        destination=query.destination,
                        departure_date=query.departure_date,
                        return_date=query.return_date,
                        price=price,
                        currency=query.currency,
                        carrier=_extract_carrier(itinerary, carriers),
                        flight_numbers=(),
                        booking_url=option.get("items", [{}])[0].get("deepLink")
                        if option.get("items")
                        else itinerary.get("deepLink"),
                        observed_at=datetime.now(timezone.utc),
                        raw=itinerary,
                    )
                )
        return quotes

    def _dedupe(self, quotes: list[FareQuote]) -> list[FareQuote]:
        seen: set[tuple[str, str, str, str | None, str, str | None]] = set()
        unique: list[FareQuote] = []
        for quote in quotes:
            key = (
                quote.origin,
                quote.destination,
                quote.departure_date.isoformat(),
                quote.return_date.isoformat() if quote.return_date else None,
                str(quote.price),
                quote.booking_url,
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(quote)
        return unique


def _date_payload(value) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {"year": value.year, "month": value.month, "day": value.day}


def _values(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _extract_price(option: dict[str, Any]) -> Decimal | None:
    candidates = [
        option.get("price", {}).get("amount") if isinstance(option.get("price"), dict) else None,
        option.get("price", {}).get("value") if isinstance(option.get("price"), dict) else None,
        option.get("amount"),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return Decimal(str(candidate))
        except InvalidOperation:
            continue
    return None


def _extract_carrier(itinerary: dict[str, Any], carriers: dict[str, Any]) -> str | None:
    carrier_ids = itinerary.get("carrierIds") or itinerary.get("marketingCarrierIds") or []
    for carrier_id in carrier_ids:
        carrier = carriers.get(str(carrier_id), {})
        if isinstance(carrier, dict):
            return carrier.get("name") or carrier.get("iata")
    return None


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
    return SkyscannerFlightProvider().search(query, 8)
