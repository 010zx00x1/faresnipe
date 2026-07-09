from __future__ import annotations

import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Protocol

from faresnipe.models import FareQuote, SearchQuery
from faresnipe.providers.base import FlightProvider


class FlightFinderScraperError(RuntimeError):
    pass


class TextFetcher(Protocol):
    def fetch_text(self, url: str) -> ScrapedPage:
        pass


@dataclass(frozen=True)
class ScrapedPage:
    text: str
    final_url: str


class FlightFinderScraperProvider(FlightProvider):
    name = "flight_finder_scraper"

    def __init__(
        self,
        fetcher: TextFetcher | None = None,
        country: str | None = None,
        language: str | None = None,
        settle_seconds: float | None = None,
    ) -> None:
        self.country = (country or os.environ.get("FARESNIPE_SCRAPER_COUNTRY", "CL")).upper()
        self.language = language or os.environ.get("FARESNIPE_SCRAPER_LANGUAGE", "en")
        self.settle_seconds = (
            settle_seconds
            if settle_seconds is not None
            else float(os.environ.get("FARESNIPE_SCRAPER_SETTLE_SECONDS", "8"))
        )
        self.fetcher = fetcher or PlaywrightTextFetcher(settle_seconds=self.settle_seconds)

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        last_text = ""
        last_url = ""
        for url in build_google_flights_url_candidates(
            query=query,
            country=self.country,
            language=self.language,
        ):
            page = self.fetcher.fetch_text(url)
            last_text = page.text
            last_url = page.final_url
            if page_has_requested_route(page.text, query.origin, query.destination):
                quotes = parse_google_flights_text(page.text, query, page.final_url, max_results)
                if quotes:
                    return quotes

        return parse_google_flights_text(last_text, query, last_url, max_results)


class PlaywrightTextFetcher:
    def __init__(self, settle_seconds: float = 8, timeout_seconds: int | None = None) -> None:
        self.settle_seconds = settle_seconds
        self.timeout_seconds = timeout_seconds or int(os.environ.get("FARESNIPE_SCRAPER_TIMEOUT_SECONDS", "45"))

    def fetch_text(self, url: str) -> ScrapedPage:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FlightFinderScraperError(
                "Playwright is required for provider='flight_finder_scraper'. "
                "Install it with: python3 -m pip install '.[scraping]' && "
                "python3 -m playwright install chromium"
            ) from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            try:
                context = browser.new_context(
                    locale="es-CL",
                    timezone_id="America/Santiago",
                    viewport={"width": 1440, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    extra_http_headers={"Accept-Language": "es-CL,es;q=0.9,en;q=0.8"},
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_seconds * 1000)
                _accept_google_consent(page)
                time.sleep(self.settle_seconds)
                text = page.locator("body").inner_text(timeout=10_000)
                return ScrapedPage(text=text, final_url=page.url)
            finally:
                browser.close()


def build_google_flights_url_candidates(
    query: SearchQuery,
    country: str = "CL",
    language: str = "en",
) -> list[str]:
    date_from = query.departure_date.isoformat()
    date_to = query.return_date.isoformat() if query.return_date else None
    one_way = query.return_date is None
    prefix = "one way " if one_way else ""

    phrases = [
        f"{prefix}flights from {query.origin} to {query.destination} on {date_from}"
        if one_way
        else f"flights from {query.origin} to {query.destination} on {date_from} to {date_to}",
        f"{prefix}{query.origin} to {query.destination} {date_from}"
        if one_way
        else f"{query.origin} to {query.destination} {date_from} to {date_to}",
        f"{prefix}flights to {query.destination} from {query.origin} departing {date_from}"
        if one_way
        else f"flights to {query.destination} from {query.origin} departing {date_from} returning {date_to}",
    ]
    return [_build_google_flights_url(phrase, query.currency, country, language) for phrase in phrases]


def parse_google_flights_text(
    text: str,
    query: SearchQuery,
    final_url: str,
    max_results: int,
) -> list[FareQuote]:
    normalized = _normalize_text(text)
    blocks = _extract_flight_blocks(normalized, query.currency)
    quotes: list[FareQuote] = []
    seen: set[Decimal] = set()
    for block in blocks:
        if block.price in seen:
            continue
        seen.add(block.price)
        quotes.append(
            FareQuote(
                provider=FlightFinderScraperProvider.name,
                origin=query.origin,
                destination=query.destination,
                departure_date=query.departure_date,
                return_date=query.return_date,
                price=block.price,
                currency=query.currency,
                carrier=block.carrier,
                stops=block.stops,
                duration=block.duration,
                departure_time=block.departure_time,
                arrival_time=block.arrival_time,
                booking_url=final_url,
                observed_at=datetime.now(timezone.utc),
                raw={
                    "source": "google_flights_visible_text",
                    "block_text": block.text[:500],
                },
            )
        )
        if len(quotes) >= max_results:
            break
    quotes.sort(key=lambda quote: quote.price)
    return quotes


def page_has_requested_route(text: str, origin: str, destination: str) -> bool:
    normalized = _normalize_text(text).upper()
    origin = origin.upper()
    destination = destination.upper()
    patterns = [
        rf"\b{re.escape(origin)}\b\s*(?:TO|A|->|→|-|–|—)\s*\b{re.escape(destination)}\b",
        rf"\bFROM\s+{re.escape(origin)}\b[\s\S]{{0,120}}\bTO\s+{re.escape(destination)}\b",
        rf"\bDESDE\s+{re.escape(origin)}\b[\s\S]{{0,120}}\bA\s+{re.escape(destination)}\b",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


@dataclass(frozen=True)
class FlightTextBlock:
    price: Decimal
    text: str
    carrier: str | None
    stops: int | None
    duration: str | None
    departure_time: str | None
    arrival_time: str | None


def _build_google_flights_url(phrase: str, currency: str, country: str, language: str) -> str:
    params = {
        "q": phrase,
        "curr": currency,
        "gl": country,
        "hl": language,
    }
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode(params)


def _accept_google_consent(page) -> None:  # type: ignore[no-untyped-def]
    for label in ("Accept all", "I agree", "Aceptar todo", "Acepto"):
        try:
            button = page.locator(f"button:has-text('{label}')").first
            if button.is_visible(timeout=1200):
                button.click(timeout=1200)
                return
        except Exception:
            continue


def _normalize_text(text: str) -> str:
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_price_candidates(text: str, currency: str) -> list[Decimal]:
    return [block.price for block in _extract_flight_blocks(text, currency)]


def _extract_flight_blocks(text: str, currency: str) -> list[FlightTextBlock]:
    currency = currency.upper()
    patterns = _currency_patterns(currency)
    blocks: list[FlightTextBlock] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if _is_price_insight_context(text, match.start(), match.end()):
                continue
            price = _parse_price(match.group("amount"))
            if price is not None and _looks_like_fare(price, currency):
                block_text = _flight_block_context(text, match.start(), match.end())
                if not _looks_like_flight_block(block_text):
                    continue
                blocks.append(
                    FlightTextBlock(
                        price=price,
                        text=block_text,
                        carrier=_extract_carrier(block_text),
                        stops=_extract_stops(block_text),
                        duration=_extract_duration(block_text),
                        departure_time=_extract_departure_time(block_text),
                        arrival_time=_extract_arrival_time(block_text),
                    )
                )
    blocks.sort(key=lambda block: block.price)
    return blocks


def _currency_patterns(currency: str) -> list[str]:
    escaped = re.escape(currency)
    if currency == "USD":
        return [
            rf"(?<![A-Z])USD[^\d]{{0,5}}(?P<amount>\d[\d,]*(?:\.\d{{1,2}})?)",
            rf"US\$[^\d]{{0,5}}(?P<amount>\d[\d,]*(?:\.\d{{1,2}})?)",
            rf"\$[^\d]{{0,5}}(?P<amount>\d[\d,]*(?:\.\d{{1,2}})?)",
        ]
    if currency == "CLP":
        return [
            rf"(?<![A-Z])CLP[^\d]{{0,5}}(?P<amount>\d[\d,.]*)",
            rf"\$[^\d]{{0,5}}(?P<amount>\d[\d.]*)",
        ]
    return [rf"(?<![A-Z]){escaped}[^\d]{{0,5}}(?P<amount>\d[\d,]*(?:\.\d{{1,2}})?)"]


def _parse_price(value: str) -> Decimal | None:
    cleaned = value.strip()
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "." in cleaned and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", cleaned):
        cleaned = cleaned.replace(".", "")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _is_price_insight_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 90):start].lower()
    after = text[end:end + 45].lower()
    if "prices are currently" in before or "price insights" in before:
        return True
    after_phrases = (
        "cheaper than usual",
        "more expensive than usual",
        "higher than usual",
        "lower than usual",
    )
    return any(phrase in after for phrase in after_phrases)


def _flight_block_context(text: str, start: int, end: int) -> str:
    before = text[max(0, start - 360):start]
    last_boundary = max(before.lower().rfind("round trip"), before.lower().rfind("one way"))
    if last_boundary != -1:
        before = before[last_boundary + len("round trip"):]
    after = text[end:end + 80]
    return _normalize_text(f"{before} {text[start:end]} {after}")


def _looks_like_flight_block(text: str) -> bool:
    has_airport_pair = bool(re.search(r"\b[A-Z]{3}\s*[?→\-–—]\s*[A-Z]{3}\b", text))
    has_flight_signal = bool(
        re.search(
            r"\b(?:nonstop|direct|\d+\s+stops?|operated by|\d+\s+hr(?:\s+\d+\s+min)?)\b",
            text,
            flags=re.IGNORECASE,
        )
    )
    return has_airport_pair or has_flight_signal


def _extract_carrier(text: str) -> str | None:
    known = (
        "LATAM",
        "Sky Airline",
        "JetSMART",
        "Iberia",
        "American",
        "Delta",
        "United",
        "Copa",
        "Avianca",
        "Aerolineas Argentinas",
        "British Airways",
        "Air France",
        "KLM",
    )
    lower = text.lower()
    for carrier in known:
        if carrier.lower() in lower:
            return carrier
    match = re.search(r"\b([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)\s+Operated by\b", text)
    if match:
        return match.group(1).strip()
    return None


def _extract_stops(text: str) -> int | None:
    if re.search(r"\bnonstop\b|\bdirect\b", text, flags=re.IGNORECASE):
        return 0
    match = re.search(r"\b(\d+)\s+stops?\b", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_duration(text: str) -> str | None:
    match = re.search(r"\b(\d+\s+hr(?:\s+\d+\s+min)?)\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\b(\d+h(?:\s*\d+m)?)\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _extract_departure_time(text: str) -> str | None:
    times = _extract_times(text)
    return times[0] if times else None


def _extract_arrival_time(text: str) -> str | None:
    times = _extract_times(text)
    return times[1] if len(times) > 1 else None


def _extract_times(text: str) -> list[str]:
    return re.findall(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b", text, flags=re.IGNORECASE)


def _looks_like_fare(price: Decimal, currency: str) -> bool:
    if currency.upper() == "CLP":
        return Decimal("1000") <= price <= Decimal("20000000")
    return Decimal("10") <= price <= Decimal("20000")
