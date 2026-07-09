from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


class TripKind(str, Enum):
    ROUND_TRIP = "round_trip"
    ONE_WAY = "one_way"


@dataclass(frozen=True)
class Route:
    origin: str
    destination: str
    enabled: bool = True
    max_price: Decimal | None = None
    mistake_fare_below: Decimal | None = None


@dataclass(frozen=True)
class Origin:
    code: str
    name: str
    destinations: tuple[str, ...]
    default_max_price: Decimal | None = None
    default_mistake_fare_below: Decimal | None = None
    enabled: bool = True


@dataclass(frozen=True)
class SearchQuery:
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    adults: int
    currency: str

    @property
    def trip_kind(self) -> TripKind:
        return TripKind.ROUND_TRIP if self.return_date else TripKind.ONE_WAY


@dataclass(frozen=True)
class FareQuote:
    provider: str
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    price: Decimal
    currency: str
    carrier: str | None = None
    flight_numbers: tuple[str, ...] = ()
    stops: int | None = None
    duration: str | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    booking_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def trip_kind(self) -> TripKind:
        return TripKind.ROUND_TRIP if self.return_date else TripKind.ONE_WAY


@dataclass(frozen=True)
class Baseline:
    median_price: Decimal | None
    min_price: Decimal | None
    quote_count: int


@dataclass(frozen=True)
class DealAlert:
    quote: FareQuote
    reasons: tuple[str, ...]
    severity: str
    baseline: Baseline
    discount_ratio: Decimal | None = None
