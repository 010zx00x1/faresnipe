from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from .models import DealAlert


class Notifier:
    """Sends alerts through the configured channels.

    Each channel (console, webhook, Telegram) runs in isolation: a failure in
    one channel does not prevent the others from receiving the alert or break
    the scanner iterating through quotes.
    """

    def __init__(
        self,
        console: bool = True,
        webhook_url: str | None = None,
        telegram_bot_token: str | None = None,
        telegram_chat_id: str | None = None,
        opener: Callable[..., object] = urllib.request.urlopen,
        error_sink: Callable[[str], None] | None = None,
    ) -> None:
        self.console = console
        self.webhook_url = webhook_url
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.opener = opener
        self._error_sink = error_sink or (lambda msg: print(f"faresnipe notify: {msg}", file=sys.stderr))

    def send(self, alert: DealAlert) -> None:
        payload = self._payload(alert)
        text = format_alert(alert)
        # Console: print-only and unlikely to fail, but still protected.
        if self.console:
            try:
                print(text)
            except Exception as exc:  # pragma: no cover - stdout write failure
                self._error_sink(f"console channel failed: {exc}")
        if self.webhook_url:
            try:
                self._post_webhook(payload)
            except Exception as exc:
                self._error_sink(f"webhook channel failed: {exc}")
        if self.telegram_bot_token and self.telegram_chat_id:
            try:
                self._post_telegram(text)
            except Exception as exc:
                self._error_sink(f"telegram channel failed: {exc}")

    def _payload(self, alert: DealAlert) -> dict[str, object]:
        quote = alert.quote
        return {
            "severity": alert.severity,
            "origin": quote.origin,
            "destination": quote.destination,
            "departure_date": quote.departure_date.isoformat(),
            "return_date": quote.return_date.isoformat() if quote.return_date else None,
            "price": str(quote.price),
            "currency": quote.currency,
            "carrier": quote.carrier,
            "flight_numbers": list(quote.flight_numbers),
            "baseline_median": str(alert.baseline.median_price) if alert.baseline.median_price else None,
            "discount_ratio": str(alert.discount_ratio) if alert.discount_ratio is not None else None,
            "reasons": list(alert.reasons),
        }

    def _post_webhook(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url or "",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener(request, timeout=20):
            pass

    def _post_telegram(self, text: str) -> None:
        data = json.dumps(
            {
                "chat_id": self.telegram_chat_id,
                "text": text,
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener(request, timeout=20):
            pass


def format_alert(alert: DealAlert) -> str:
    quote = alert.quote
    dates = quote.departure_date.isoformat()
    if quote.return_date:
        dates = f"{dates} -> {quote.return_date.isoformat()}"

    baseline = "no baseline yet"
    if alert.baseline.median_price:
        baseline = f"median {alert.baseline.median_price} {quote.currency} from {alert.baseline.quote_count} quotes"

    parts = [
        f"[{alert.severity.upper()}] {quote.origin}-{quote.destination} {dates}",
        f"price: {quote.price} {quote.currency}",
        f"carrier: {quote.carrier or 'unknown'}",
        f"baseline: {baseline}",
        "reasons: " + "; ".join(alert.reasons),
    ]
    details = []
    if quote.stops is not None:
        details.append("nonstop" if quote.stops == 0 else f"{quote.stops} stop(s)")
    if quote.duration:
        details.append(quote.duration)
    if quote.departure_time or quote.arrival_time:
        details.append(f"{quote.departure_time or '?'}-{quote.arrival_time or '?'}")
    if details:
        parts.append("details: " + ", ".join(details))
    if quote.flight_numbers:
        parts.append("flights: " + ", ".join(quote.flight_numbers))
    return "\n".join(parts)


def decimal_to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


__all__ = ["Notifier", "format_alert", "decimal_to_float"]
