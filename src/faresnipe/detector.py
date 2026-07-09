from __future__ import annotations

import hashlib
from decimal import Decimal

from .config import DetectionConfig
from .models import Baseline, DealAlert, FareQuote, Route


class DealDetector:
    def __init__(self, config: DetectionConfig) -> None:
        self.config = config

    def evaluate(self, quote: FareQuote, route: Route, baseline: Baseline) -> DealAlert | None:
        reasons: list[str] = []
        severity = "deal"
        discount_ratio: Decimal | None = None

        if route.max_price is not None and quote.price <= route.max_price:
            reasons.append(f"price <= route max ({quote.price} <= {route.max_price} {quote.currency})")

        if route.mistake_fare_below is not None and quote.price <= route.mistake_fare_below:
            reasons.append(
                f"price <= mistake threshold ({quote.price} <= {route.mistake_fare_below} {quote.currency})"
            )

        if baseline.median_price and baseline.quote_count >= self.config.min_history_quotes:
            discount_ratio = (baseline.median_price - quote.price) / baseline.median_price
            if discount_ratio >= self.config.discount_ratio:
                reasons.append(f"{_pct(discount_ratio)} below historical median")
            if discount_ratio >= self.config.mistake_fare_ratio:
                severity = "mistake_fare"
                reasons.append(f"{_pct(discount_ratio)} below median; possible fare error")

        if not reasons:
            return None

        return DealAlert(
            quote=quote,
            reasons=tuple(reasons),
            severity=severity,
            baseline=baseline,
            discount_ratio=discount_ratio,
        )


def alert_fingerprint(alert: DealAlert) -> str:
    quote = alert.quote
    raw = "|".join(
        [
            quote.provider,
            quote.origin,
            quote.destination,
            quote.departure_date.isoformat(),
            quote.return_date.isoformat() if quote.return_date else "",
            str(quote.price),
            quote.currency,
            alert.severity,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pct(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.1'))}%"
