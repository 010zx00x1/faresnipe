from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from .config import AppConfig
from .detector import DealDetector, alert_fingerprint
from .models import DealAlert, SearchQuery
from .notify import Notifier
from .storage import FareStore

LOGGER = logging.getLogger(__name__)
PLANNED_SEARCH_WARNING_THRESHOLD = 1000


@dataclass(frozen=True)
class ScanStats:
    searches: int = 0
    quotes: int = 0
    alerts: int = 0
    failures: int = 0


class FlightScanner:
    def __init__(
        self,
        config: AppConfig,
        store: FareStore,
        notifier: Notifier,
        provider: object | None = None,
        providers: tuple[object, ...] | list[object] | None = None,
    ) -> None:
        self.config = config
        if providers is not None:
            self.providers = tuple(providers)
        elif provider is not None:
            self.providers = (provider,)
        else:
            raise ValueError("FlightScanner requires at least one provider.")
        if not self.providers:
            raise ValueError("FlightScanner requires at least one provider.")
        self.provider = self.providers[0]
        self.store = store
        self.notifier = notifier
        self.detector = DealDetector(config.detection)

    def run_once(
        self,
        limit_searches: int | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> ScanStats:
        searches = 0
        quotes_count = 0
        alerts_count = 0
        failures_count = 0
        planned = self.planned_searches(limit_searches)
        unbounded_planned = self.planned_searches(None)
        if unbounded_planned > PLANNED_SEARCH_WARNING_THRESHOLD:
            LOGGER.warning(
                "Large scan planned: %s searches across %s routes and %s providers%s.",
                unbounded_planned,
                sum(1 for route in self.config.routes if route.enabled),
                len(self.providers),
                f" (capped to {planned} by limit_searches)" if planned != unbounded_planned else "",
            )

        route_queries = [
            (route, self._queries_for_route(route.origin, route.destination))
            for route in self.config.routes
            if route.enabled
        ]
        max_queries_per_route = max((len(queries) for _, queries in route_queries), default=0)

        for query_index in range(max_queries_per_route):
            for route, queries in route_queries:
                if query_index >= len(queries):
                    continue
                query = queries[query_index]
                for provider in self.providers:
                    if limit_searches is not None and searches >= limit_searches:
                        return ScanStats(
                            searches=searches,
                            quotes=quotes_count,
                            alerts=alerts_count,
                            failures=failures_count,
                        )

                    if progress_callback:
                        progress_callback(
                            {
                                "event": "search_started",
                                "query": query,
                                "provider": provider.name,
                                "searches": searches,
                                "quotes": quotes_count,
                                "alerts": alerts_count,
                                "failures": failures_count,
                                "total_searches": planned,
                            }
                        )
                    searches += 1
                    try:
                        quotes = provider.search(query, self.config.scanner.max_results_per_search)
                    except Exception as exc:
                        failures_count += 1
                        if progress_callback:
                            progress_callback(
                                {
                                    "event": "search_failed",
                                    "query": query,
                                    "provider": provider.name,
                                    "searches": searches,
                                    "quotes": quotes_count,
                                    "alerts": alerts_count,
                                    "failures": failures_count,
                                    "total_searches": planned,
                                    "error": str(exc),
                                }
                            )
                        if searches < planned and self.config.scanner.request_delay_seconds > 0:
                            time.sleep(self.config.scanner.request_delay_seconds)
                        continue
                    quotes_count += len(quotes)

                    for quote in quotes:
                        baseline = self.store.baseline_for(quote, self.config.detection.history_days)
                        alert = self.detector.evaluate(quote, route, baseline)
                        self.store.save_quote(quote, alert=alert, baseline=baseline)
                        if alert and self._should_send(alert):
                            self.notifier.send(alert)
                            self.store.save_alert(alert_fingerprint(alert))
                            alerts_count += 1

                    if progress_callback:
                        progress_callback(
                            {
                                "event": "search_completed",
                                "query": query,
                                "provider": provider.name,
                                "searches": searches,
                                "quotes": quotes_count,
                                "alerts": alerts_count,
                                "failures": failures_count,
                                "total_searches": planned,
                            }
                        )
                    if searches < planned and self.config.scanner.request_delay_seconds > 0:
                        time.sleep(self.config.scanner.request_delay_seconds)

        return ScanStats(
            searches=searches,
            quotes=quotes_count,
            alerts=alerts_count,
            failures=failures_count,
        )

    def planned_searches(self, limit_searches: int | None = None) -> int:
        total = sum(
            len(self._queries_for_route(route.origin, route.destination))
            for route in self.config.routes
            if route.enabled
        ) * len(self.providers)
        if limit_searches is None:
            return total
        return min(total, limit_searches)

    def _queries_for_route(self, origin: str, destination: str) -> list[SearchQuery]:
        today = date.today()
        queries: list[SearchQuery] = []
        for offset in range(
            self.config.scanner.days_ahead_start,
            self.config.scanner.days_ahead_end + 1,
        ):
            departure = today + timedelta(days=offset)
            for stay_length in self.config.scanner.stay_lengths:
                queries.append(
                    SearchQuery(
                        origin=origin,
                        destination=destination,
                        departure_date=departure,
                        return_date=departure + timedelta(days=stay_length),
                        adults=self.config.scanner.adults,
                        currency=self.config.scanner.currency,
                    )
                )
        return queries

    def _should_send(self, alert: DealAlert) -> bool:
        return not self.store.latest_alert_fingerprint_seen(alert_fingerprint(alert), hours=24)
