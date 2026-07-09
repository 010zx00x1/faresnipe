from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlencode

from faresnipe.models import FareQuote, SearchQuery
from faresnipe.providers.base import FlightProvider


class GoogleFlightsScraplingError(RuntimeError):
    pass


class ScraplingPageFetcher(Protocol):
    def fetch(self, url: str) -> Any:
        """Return a Scrapling page/response for the rendered URL."""


class StealthyScraplingFetcher:
    def __init__(
        self,
        wait_ms: int | None = None,
        timeout_ms: int | None = None,
        locale: str | None = None,
        timezone_id: str | None = None,
    ) -> None:
        self.wait_ms = wait_ms if wait_ms is not None else int(os.environ.get("FARESNIPE_SCRAPLING_WAIT_MS", "5000"))
        self.timeout_ms = (
            timeout_ms if timeout_ms is not None else int(os.environ.get("FARESNIPE_SCRAPLING_TIMEOUT_MS", "45000"))
        )
        self.locale = locale or os.environ.get("FARESNIPE_SCRAPLING_LOCALE", "es-419")
        self.timezone_id = timezone_id or os.environ.get("FARESNIPE_SCRAPLING_TIMEZONE", "America/Santiago")

    def fetch(self, url: str) -> Any:
        try:
            from scrapling import StealthyFetcher
        except ImportError as exc:
            raise GoogleFlightsScraplingError(
                "scrapling[fetchers] is required for provider='google_flights_scrapling'. "
                "Install it with: python3 -m pip install 'scrapling[fetchers]>=0.4.10'"
            ) from exc

        return StealthyFetcher.fetch(
            url,
            wait=self.wait_ms,
            timeout=self.timeout_ms,
            locale=self.locale,
            timezone_id=self.timezone_id,
            network_idle=True,
        )


class GoogleFlightsScraplingProvider(FlightProvider):
    name = "google_flights_scrapling"

    def __init__(self, fetcher: ScraplingPageFetcher | None = None) -> None:
        self.fetcher = fetcher or StealthyScraplingFetcher()

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        url = google_flights_url(query)
        try:
            page = self.fetcher.fetch(url)
            quotes = _quotes_from_page(page, query, url, max_results)
        except Exception as exc:
            if isinstance(exc, GoogleFlightsScraplingError):
                raise
            raise GoogleFlightsScraplingError(f"Scrapling Google Flights scrape failed: {exc}") from exc

        if not quotes:
            raise GoogleFlightsScraplingError("Google Flights returned no parseable fare results.")
        return quotes


def google_flights_url(query: SearchQuery) -> str:
    search = f"Flights from {query.origin} to {query.destination} on {query.departure_date.isoformat()}"
    if query.return_date is not None:
        search += f" through {query.return_date.isoformat()}"
    if query.adults != 1:
        search += f" for {query.adults} adults"

    return "https://www.google.com/travel/flights?" + urlencode(
        {
            "q": search,
            "curr": query.currency,
            "hl": os.environ.get("FARESNIPE_SCRAPLING_HL", "es-419"),
        }
    )


def _quotes_from_page(page: Any, query: SearchQuery, booking_url: str, max_results: int) -> list[FareQuote]:
    quotes: list[FareQuote] = []
    seen: set[tuple[str, Decimal, str]] = set()

    for price_node in _price_nodes(page, query.currency):
        price = _parse_price(price_node, query.currency)
        if price is None:
            continue

        item = _result_item(price_node)
        if item is None:
            # Google also renders summary/min-price chips outside the flight list.
            continue

        item_text = _all_text(item)
        if not item_text:
            continue

        data_gs = str(_attributes(price_node).get("data-gs") or "")
        key = (data_gs, price, item_text[:300])
        if key in seen:
            continue
        seen.add(key)
        departure_time, arrival_time = _extract_times(item_text)

        quotes.append(
            FareQuote(
                provider=GoogleFlightsScraplingProvider.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=price,
                currency=query.currency,
                carrier=_extract_carrier(item_text),
                flight_numbers=_extract_flight_numbers(data_gs),
                stops=_extract_stops(item_text),
                duration=_extract_duration(item_text),
                departure_time=departure_time,
                arrival_time=arrival_time,
                booking_url=booking_url,
                observed_at=datetime.now(timezone.utc),
                raw={
                    "source": "google_flights_scrapling",
                    "selector": 'span[data-gs][role="text"], span[data-gs][aria-label]',
                    "data_gs": data_gs or None,
                    "text": item_text,
                },
            )
        )
        if len(quotes) >= max_results:
            break

    quotes.sort(key=lambda quote: quote.price)
    return quotes


def _price_nodes(page: Any, currency: str) -> list[Any]:
    selectors = [
        'span[data-gs][role="text"]',
        'span[data-gs][aria-label*="pesos"]',
        'span[data-gs][aria-label*="dollars"]',
        'span[data-gs][aria-label*="euros"]',
        "span[data-gs][aria-label]",
    ]
    nodes: list[Any] = []
    for selector in selectors:
        try:
            for node in page.css(selector):
                if node not in nodes:
                    nodes.append(node)
        except Exception:
            continue
    return nodes


def _result_item(price_node: Any) -> Any | None:
    for xpath in ("ancestor::li[1]", 'ancestor::*[@role="listitem"][1]'):
        try:
            matches = price_node.xpath(xpath)
        except Exception:
            matches = []
        if matches:
            return matches[0]
    return None


def _parse_price(node: Any, currency: str) -> Decimal | None:
    attrs = _attributes(node)
    text = _node_text(node)
    aria = str(attrs.get("aria-label") or "")
    candidates = [
        rf"\b{re.escape(currency)}\s*([0-9][0-9.,]*)",
        r"([0-9][0-9.,]*)\s*(?:pesos|dollars|euros|reales|soles|pounds)",
    ]
    haystack = f"{text} {aria}".replace("\xa0", " ")
    for pattern in candidates:
        match = re.search(pattern, haystack, flags=re.IGNORECASE)
        if not match:
            continue
        return _decimal_amount(match.group(1), currency)
    return None


def _decimal_amount(raw: str, currency: str) -> Decimal | None:
    amount = re.sub(r"[^\d.,]", "", raw)
    if not amount:
        return None
    zero_decimal = {"CLP", "JPY", "KRW", "VND"}
    if currency.upper() in zero_decimal:
        amount = re.sub(r"[^\d]", "", amount)
    elif "," in amount and "." in amount:
        amount = amount.replace(",", "")
    elif amount.count(",") == 1 and len(amount.rsplit(",", 1)[1]) == 2:
        amount = amount.replace(",", ".")
    else:
        amount = amount.replace(",", "")
    try:
        return Decimal(amount)
    except InvalidOperation:
        return None


def _extract_carrier(text: str) -> str | None:
    known = (
        "LATAM",
        "KLM",
        "JetSMART",
        "Sky Airline",
        "Aerolíneas Argentinas",
        "Avianca",
        "Copa",
        "American",
        "Delta",
        "United",
        "Iberia",
    )
    found = [name for name in known if re.search(rf"\b{re.escape(name)}\b", text, flags=re.IGNORECASE)]
    if found:
        return ", ".join(dict.fromkeys(found))

    match = re.search(
        r"(?:a\.m\.|p\.m\.)\s+([A-Z][A-Za-zÁÉÍÓÚÑáéíóúñ .,&-]{2,}?)\s+(?:Operado por|\d+\s*h|Directo|\d+\s+parada)",
        text,
    )
    return match.group(1).strip(" ,") if match else None


def _extract_flight_numbers(data_gs: str) -> tuple[str, ...]:
    # Google often encodes flight numbers in data-gs as text after decoding, but
    # the attribute is opaque enough that keeping it raw is safer than guessing.
    return ()


def _extract_stops(text: str) -> int | None:
    if re.search(r"\bDirecto\b", text, flags=re.IGNORECASE):
        return 0
    match = re.search(r"(\d+)\s*parada", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_duration(text: str) -> str | None:
    match = re.search(r"\b(\d+\s*h(?:\s*\d+\s*min)?|\d+\s*min)\b", text)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def _extract_times(text: str) -> tuple[str | None, str | None]:
    matches = re.findall(r"(\d{1,2}):(\d{2})\s*([ap])\.m\.", text, flags=re.IGNORECASE)
    normalized: list[str] = []
    for hour_text, minute_text, period in matches:
        hour = int(hour_text)
        if period.lower() == "p" and hour != 12:
            hour += 12
        if period.lower() == "a" and hour == 12:
            hour = 0
        value = f"{hour:02d}:{int(minute_text):02d}"
        if not normalized or normalized[-1] != value:
            normalized.append(value)
    departure = normalized[0] if normalized else None
    arrival = normalized[1] if len(normalized) > 1 else None
    return departure, arrival


def _all_text(node: Any) -> str:
    if hasattr(node, "get_all_text"):
        return re.sub(r"\s+", " ", node.get_all_text(separator=" ", strip=True)).strip()
    return re.sub(r"\s+", " ", _node_text(node)).strip()


def _node_text(node: Any) -> str:
    value = getattr(node, "text", "")
    if callable(value):
        value = value()
    return str(value or "")


def _attributes(node: Any) -> dict[str, Any]:
    attrs = getattr(node, "attrib", {}) or {}
    return dict(attrs)
