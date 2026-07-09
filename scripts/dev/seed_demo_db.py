from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from faresnipe.models import Baseline, FareQuote, SearchQuery  # noqa: E402
from faresnipe.providers.google_flights_structured import google_flights_booking_url  # noqa: E402
from faresnipe.storage import FareStore  # noqa: E402

DB_PATH = ROOT / "data" / "faresnipe-demo.sqlite3"


@dataclass(frozen=True)
class SeedFare:
    origin: str
    destination: str
    days_until_departure: int
    stay_days: int
    price: str
    currency: str
    carrier: str
    flight_numbers: tuple[str, ...]
    severity: str
    baseline_median: str
    recorded_days_ago: float


@dataclass(frozen=True)
class SeedAlert:
    severity: str


FARES = (
    SeedFare("SCL", "EZE", 42, 7, "89", "USD", "LA", ("LA455", "LA454"), "mistake_fare", "405", 0.04),
    SeedFare("AEP", "SCL", 25, 5, "35", "USD", "SK", ("SK931", "SK932"), "mistake_fare", "124", 0.16),
    SeedFare("SCL", "MAD", 88, 14, "287", "USD", "IB", ("IB6830", "IB6831"), "mistake_fare", "1080", 0.8),
    SeedFare("SCL", "MIA", 116, 10, "199", "USD", "AA", ("AA912", "AA957"), "mistake_fare", "930", 3),
    SeedFare("SCL", "LIM", 38, 7, "124", "USD", "LA", ("LA2370", "LA2371"), "deal", "295", 4),
    SeedFare("AEP", "PMC", 31, 6, "99", "USD", "JA", ("JA320", "JA321"), "deal", "178", 9),
    SeedFare("SCL", "JFK", 140, 12, "640", "USD", "AA", ("AA940", "AA945"), "deal", "1010", 15),
    SeedFare("AEP", "BBA", 74, 7, "295", "USD", "LA", ("LA80", "LA81"), "deal", "455", 22),
    SeedFare("SCL", "BOG", 65, 8, "210", "USD", "AV", ("AV98", "AV97"), "deal", "340", 31),
    SeedFare("SCL", "EZE", 58, 5, "412", "USD", "LA", ("LA477", "LA476"), "baseline", "405", 45),
    SeedFare("SCL", "MIA", 154, 11, "965", "USD", "AA", ("AA912", "AA957"), "baseline", "930", 63),
    SeedFare("AEP", "SCL", 19, 4, "118", "USD", "SK", ("SK933", "SK934"), "baseline", "124", 70),
    SeedFare("AEP", "PMC", 47, 7, "184", "USD", "LA", ("LA286", "LA287"), "baseline", "178", 82),
    SeedFare("SCL", "GRU", 122, 9, "355", "USD", "LA", ("LA8205", "LA8206"), "baseline", "370", 89),
)


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    store = FareStore(DB_PATH)
    now = datetime.now(timezone.utc)
    today = now.date()

    for fare in FARES:
        observed_at = now - timedelta(days=fare.recorded_days_ago)
        departure = today + timedelta(days=fare.days_until_departure)
        return_date = departure + timedelta(days=fare.stay_days)
        query = SearchQuery(
            origin=fare.origin,
            destination=fare.destination,
            departure_date=departure,
            return_date=return_date,
            adults=1,
            currency=fare.currency,
        )
        quote = FareQuote(
            provider="google_flights_structured",
            origin=fare.origin,
            destination=fare.destination,
            departure_date=departure,
            return_date=return_date,
            price=Decimal(fare.price),
            currency=fare.currency,
            carrier=fare.carrier,
            flight_numbers=fare.flight_numbers,
            booking_url=google_flights_booking_url(query),
            observed_at=observed_at,
        )
        baseline = Baseline(Decimal(fare.baseline_median), None, 8)
        alert = None if fare.severity == "baseline" else SeedAlert(fare.severity)
        store.save_quote(quote, alert=alert, baseline=baseline)

    rel_path = os.path.relpath(DB_PATH, ROOT)
    print(f"Wrote {len(FARES)} quotes to {rel_path}")


if __name__ == "__main__":
    main()
