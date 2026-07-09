"""Lógica de payloads y orquestación del dashboard HTTP de faresnipe.

El dashboard sólo dispara escaneos manuales desde la UI; el watch continuo lo
maneja el CLI (``faresnipe --watch``) o systemd. No hay thread de auto-scan.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from decimal import Decimal
from typing import Any

from ..config import AppConfig
from ..models import Origin, SearchQuery
from ..notify import Notifier
from ..providers import build_providers
from ..scanner import FlightScanner
from ..storage import FareStore


class DashboardServer:
    """Construye los payloads JSON que consume la UI y dispara escaneos manuales."""
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = FareStore(config.scanner.database_path)
        self.store.mark_interrupted_scan_runs()
        self.scan_lock = threading.Lock()
        self.last_scan: dict[str, Any] | None = None
    def summary(self) -> dict[str, Any]:
        s = self.config.scanner
        stats_rows = _rows(self.store.route_stats(currency=s.currency, providers=s.provider_names))
        latest = _rows(self.store.recent_quotes(1, currency=s.currency, providers=s.provider_names))
        latest_scan = self.last_scan or _scan_run_payload(self.store.latest_scan_run())
        return {
            "database": str(s.database_path),
            "provider": s.provider,
            "providers": list(s.provider_names),
            "provider_labels": [_provider_label(p) for p in s.provider_names],
            "provider_label": _provider_label(s.provider),
            "routes_configured": sum(1 for r in self.config.routes if r.enabled),
            "routes_seen": len(stats_rows),
            "samples": sum(int(row["samples"]) for row in stats_rows),
            "latest_observed_at": latest[0]["observed_at"] if latest else None,
            "last_scan": latest_scan,
            "scan_running": self.scan_lock.locked(),
            "scan_mode": "Manual desde dashboard",
            "watch_interval_minutes": s.scan_interval_minutes,
            "watch_jitter_seconds": s.scan_jitter_seconds,
            "request_delay_seconds": s.request_delay_seconds,
        }

    def run_scan(
        self,
        limit_searches: int,
        provider_names: tuple[str, ...] | list[str],
        origin: str | None = None,
    ) -> dict[str, Any]:
        if not self.scan_lock.acquire(blocking=False):
            return {"error": "scan already running"}
        run_id: int | None = None
        provider_names = tuple(provider_names)
        provider_label = ",".join(provider_names)
        try:
            scan_config = self.config
            if origin:
                routes = tuple(r for r in self.config.routes if r.enabled and r.origin == origin)
                if not routes:
                    return {"error": f"No hay rutas configuradas desde {origin}."}
                scan_config = replace(self.config, routes=routes)
            providers = build_providers(provider_names)
            scanner = FlightScanner(config=scan_config, providers=providers, store=self.store, notifier=Notifier(console=False))
            total_searches = scanner.planned_searches(limit_searches)
            run_id = self.store.begin_scan_run(
                provider=provider_label, limit_searches=limit_searches, total_searches=total_searches,
            )
            self.last_scan = {
                "status": "running", "provider": provider_label, "providers": list(provider_names),
                "limit_searches": limit_searches, "total_searches": total_searches, "id": run_id,
            }
            stats = scanner.run_once(limit_searches=limit_searches, progress_callback=self._make_progress(run_id))
            status = _scan_status(stats.searches, stats.failures)
            result = {
                "searches": stats.searches, "quotes": stats.quotes, "alerts": stats.alerts,
                "failures": stats.failures, "provider": provider_label,
                "providers": list(provider_names), "status": status, "id": run_id, "total_searches": total_searches,
            }
            self.store.update_scan_run(run_id, status=status, searches=stats.searches, quotes=stats.quotes, alerts=stats.alerts, failures=stats.failures, complete=True, clear_current=True)
            self.last_scan = result
            return result
        except Exception as exc:
            result = {"error": str(exc), "provider": provider_label, "providers": list(provider_names), "status": "failed", "id": run_id}
            if run_id is not None:
                self.store.update_scan_run(run_id, status="failed", error=str(exc), complete=True, clear_current=True)
            self.last_scan = result
            return result
        finally:
            self.scan_lock.release()
    def _make_progress(self, run_id: int):
        def progress(event: dict[str, object]) -> None:
            query = event.get("query")
            self.store.update_scan_run(
                run_id, searches=int(event.get("searches") or 0), quotes=int(event.get("quotes") or 0),
                alerts=int(event.get("alerts") or 0), failures=int(event.get("failures") or 0),
                current_query=query if isinstance(query, SearchQuery) else None,
                error=str(event.get("error")) if event.get("error") else None,
            )
            if event.get("event") == "search_failed" and isinstance(query, SearchQuery):
                self.store.save_scan_failure(run_id, query, str(event.get("error") or "unknown error"), provider=str(event.get("provider") or ""))
            current = _scan_run_payload(self.store.latest_scan_run())
            if current:
                self.last_scan = current
        return progress
    def config_payload(self) -> dict[str, Any]:
        s, d = self.config.scanner, self.config.detection
        origins = self._configured_origins()
        route_lookup = {_route_key(r.origin, r.destination): r for r in self.config.routes}
        return {
            "scanner": {
                "provider": s.provider, "providers": list(s.provider_names),
                "provider_label": _provider_label(s.provider),
                "provider_labels": [_provider_label(p) for p in s.provider_names],
                "currency": s.currency, "days_ahead_start": s.days_ahead_start,
                "days_ahead_end": s.days_ahead_end, "stay_lengths": list(s.stay_lengths),
                "request_delay_seconds": s.request_delay_seconds,
                "scan_interval_minutes": s.scan_interval_minutes,
                "scan_jitter_seconds": s.scan_jitter_seconds,
                "max_results_per_search": s.max_results_per_search,
            },
            "detection": {
                "discount_ratio": str(d.discount_ratio),
                "mistake_fare_ratio": str(d.mistake_fare_ratio),
                "min_history_quotes": d.min_history_quotes,
                "history_days": d.history_days,
            },
            "origins": [{
                "code": origin.code, "name": origin.name,
                "destinations": list(origin.destinations),
                "default_max_price": (
                    str(origin.default_max_price)
                    if origin.default_max_price is not None else None
                ),
                "default_mistake_fare_below": (
                    str(origin.default_mistake_fare_below)
                    if origin.default_mistake_fare_below is not None else None
                ),
                "route_count": sum(
                    1 for route in self.config.routes
                    if route.enabled and route.origin == origin.code
                ),
                "enabled": origin.enabled,
                "thresholds": [
                    {
                        "origin": route.origin, "destination": route.destination,
                        "max_price": str(route.max_price) if route.max_price is not None else None,
                        "mistake_fare_below": (
                            str(route.mistake_fare_below)
                            if route.mistake_fare_below is not None else None
                        ),
                    }
                    for destination in origin.destinations
                    for route in [route_lookup.get(_route_key(origin.code, destination))]
                    if route is not None and _route_has_custom_threshold(route, origin)
                ],
            } for origin in origins],
            "routes": [{
                "origin": r.origin, "destination": r.destination, "enabled": r.enabled,
                "max_price": str(r.max_price) if r.max_price is not None else None,
                "mistake_fare_below": str(r.mistake_fare_below) if r.mistake_fare_below is not None else None,
            } for r in self.config.routes],
        }
    def enrich_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        s = self.config.scanner
        route_stats = {
            _route_key(row["origin"], row["destination"]): row
            for row in _rows(self.store.route_stats(currency=s.currency, providers=s.provider_names))
        }
        route_config = {_route_key(r.origin, r.destination): r for r in self.config.routes}
        enriched: list[dict[str, Any]] = []
        for row in rows:
            key = _route_key(row.get("origin", ""), row.get("destination", ""))
            config = route_config.get(key)
            stats = route_stats.get(key, {})
            item = dict(row)
            item["origin_name"] = self._origin_name(str(item.get("origin") or ""))
            item["provider_label"] = _provider_label(str(item.get("provider") or s.provider))
            item["route_samples"] = stats.get("samples", 0)
            item["route_min_price"] = stats.get("min_price")
            item["max_price"] = str(config.max_price) if config and config.max_price is not None else None
            item["mistake_fare_below"] = (
                str(config.mistake_fare_below)
                if config and config.mistake_fare_below is not None else None
            )
            item.update(_classify_row(item, config))
            item["quote_type"] = _quote_type(item)
            item["quote_type_label"] = _quote_type_label(item["quote_type"])
            enriched.append(item)
        return enriched
    def configured_routes_payload(self) -> list[dict[str, Any]]:
        s = self.config.scanner
        latest_rows = self.enrich_rows(_rows(self.store.latest_by_route(
            currency=s.currency, providers=s.provider_names,
        )))
        rows_by_route: dict[str, list[dict[str, Any]]] = {}
        for row in latest_rows:
            rows_by_route.setdefault(_route_key(row["origin"], row["destination"]), []).append(row)
        route_stats = {
            _route_key(row["origin"], row["destination"]): row
            for row in _rows(self.store.route_stats(currency=s.currency, providers=s.provider_names))
        }
        payload: list[dict[str, Any]] = []
        for route in self.config.routes:
            if not route.enabled:
                continue
            key = _route_key(route.origin, route.destination)
            candidates = rows_by_route.get(key, [])
            if candidates:
                best = sorted(candidates, key=_route_offer_sort_key)[0]
                item = dict(best)
                item["has_price"] = True; item["configured"] = True; item["route_enabled"] = True
                item["offers_available"] = len(candidates)
                payload.append(item)
                continue
            stats = route_stats.get(key, {})
            payload.append({
                "origin": route.origin, "destination": route.destination, "price": None,
                "origin_name": self._origin_name(route.origin),
                "currency": s.currency, "carrier": None, "stops": None, "duration": None,
                "departure_time": None, "arrival_time": None, "booking_url": None,
                "provider": None, "provider_label": None, "observed_at": None,
                "departure_date": None, "return_date": None,
                "route_samples": stats.get("samples", 0), "route_min_price": stats.get("min_price"),
                "max_price": str(route.max_price) if route.max_price is not None else None,
                "mistake_fare_below": (
                    str(route.mistake_fare_below) if route.mistake_fare_below is not None else None
                ),
                "status_kind": "unscanned", "status_label": "Pendiente",
                "opportunity_score": "0", "threshold_delta": None, "historical_delta": None,
                "historical_delta_pct": None, "opportunity_note": "Pendiente de escaneo",
                "has_price": False, "configured": True, "route_enabled": True, "offers_available": 0,
            })
        return payload
    def scan_runs_payload(self, limit: int = 10) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for row in self.store.recent_scan_runs(limit):
            payload = _scan_run_payload(row)
            if payload is None:
                continue
            payload["failure_details"] = [
                _scan_failure_payload(failure)
                for failure in self.store.scan_failures(int(payload["id"]), limit=3)
            ]
            payloads.append(payload)
        return payloads
    def scan_failures_payload(
        self, run_id: int | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        return [_scan_failure_payload(row) for row in self.store.scan_failures(run_id, limit)]
    def opportunities_payload(self) -> list[dict[str, Any]]:
        """Top oportunidades (discount vs mediana). 3 estados: ``mistake`` /
        ``deal`` / ``opportunity`` (este último para 10% ≤ d < discount_ratio)."""
        s, d = self.config.scanner, self.config.detection
        rows = self.store.top_opportunities(
            limit=20, min_discount=Decimal("0.10"),
            min_history=max(2, d.min_history_quotes), history_days=d.history_days,
            currency=s.currency, providers=s.provider_names,
        )
        route_config = {_route_key(r.origin, r.destination): r for r in self.config.routes}
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["provider_label"] = _provider_label(str(item.get("provider") or ""))
            discount = Decimal(str(item["discount_ratio"]))
            if discount >= d.mistake_fare_ratio:
                item["status_kind"] = "mistake"; item["status_label"] = "Posible error"
            elif discount >= d.discount_ratio:
                item["status_kind"] = "deal"; item["status_label"] = "Buen precio"
            else:
                item["status_kind"] = "opportunity"; item["status_label"] = "Oportunidad"
            item["opportunity_score"] = str((discount * Decimal("100")).quantize(Decimal("0.1")))
            median_value = int(Decimal(item["median_price"]))
            median_formatted = f"{median_value:,}".replace(",", ".")
            item["opportunity_note"] = f"{item['discount_pct']} bajo mediana ({median_formatted} {item['currency']}, n={item['baseline_count']})"
            route = route_config.get(_route_key(item["origin"], item["destination"]))
            item["origin_name"] = self._origin_name(str(item.get("origin") or ""))
            if route and route.max_price is not None:
                item["max_price"] = str(route.max_price)
            if route and route.mistake_fare_below is not None:
                item["mistake_fare_below"] = str(route.mistake_fare_below)
            item["has_price"] = True
            item["quote_type"] = _quote_type(item)
            item["quote_type_label"] = _quote_type_label(item["quote_type"])
            out.append(item)
        return out

    def origins_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "code": origin.code,
                "name": origin.name,
                "destinations": list(origin.destinations),
                "default_max_price": (
                    str(origin.default_max_price) if origin.default_max_price is not None else None
                ),
                "default_mistake_fare_below": (
                    str(origin.default_mistake_fare_below)
                    if origin.default_mistake_fare_below is not None else None
                ),
                "enabled": origin.enabled,
                "route_count": sum(
                    1 for route in self.config.routes
                    if route.enabled and route.origin == origin.code
                ),
            }
            for origin in self._configured_origins()
        ]

    def compare_origins_payload(
        self,
        destination: str,
        departure_date: str | None,
        return_date: str | None,
        currency: str | None = None,
    ) -> list[dict[str, Any]]:
        s = self.config.scanner
        destination = destination.upper()
        selected_currency = (currency or s.currency).upper()
        route_stats = {
            _route_key(row["origin"], row["destination"]): row
            for row in _rows(self.store.route_stats(currency=selected_currency, providers=s.provider_names))
        }
        route_config = {_route_key(r.origin, r.destination): r for r in self.config.routes if r.enabled}
        latest_rows = []
        rows_by_route: dict[str, list[dict[str, Any]]] = {}
        for row in _rows(self.store.latest_by_route(currency=selected_currency, providers=s.provider_names)):
            if departure_date and row.get("departure_date") != departure_date:
                continue
            if return_date and row.get("return_date") != return_date:
                continue
            latest_rows.append(row)
            rows_by_route.setdefault(
                _route_key(str(row.get("origin") or ""), str(row.get("destination") or "")),
                [],
            ).append(row)

        rows_by_origin: dict[str, list[dict[str, Any]]] = {}
        for row in latest_rows:
            if str(row.get("destination") or "").upper() != destination:
                continue
            rows_by_origin.setdefault(str(row.get("origin") or "").upper(), []).append(row)

        payload: list[dict[str, Any]] = []
        for origin in self._configured_origins():
            if not origin.enabled:
                continue
            key = _route_key(origin.code, destination)
            stats = route_stats.get(key, {})
            candidates = sorted(
                rows_by_origin.get(origin.code, []),
                key=lambda row: _decimal_or_none(row.get("price")) or Decimal("999999999999"),
            )
            best = candidates[0] if candidates else None
            composite = None if best else _best_composite_origin_offer(
                origin.code, destination, rows_by_route, route_stats
            )
            route = route_config.get(key)
            payload.append({
                "origin": origin.code,
                "origin_name": origin.name,
                "destination": destination,
                "cheapest_price": (
                    best.get("price") if best else composite.get("price") if composite else None
                ),
                "cheapest_carrier": (
                    best.get("carrier") if best else composite.get("carrier") if composite else None
                ),
                "cheapest_observed_at": (
                    best.get("observed_at") if best else composite.get("observed_at") if composite else None
                ),
                "cheapest_booking_url": (
                    best.get("booking_url") if best else composite.get("booking_url") if composite else None
                ),
                "samples": stats.get("samples", 0) or (composite.get("samples") if composite else 0),
                "route_min_price": stats.get("min_price") or (composite.get("route_min_price") if composite else None),
                "currency": selected_currency,
                "configured": route is not None or composite is not None,
                "via": composite.get("via") if composite else None,
                "departure_date": departure_date,
                "return_date": return_date,
            })
        payload.sort(key=lambda row: (
            _decimal_or_none(row.get("cheapest_price")) is None,
            _decimal_or_none(row.get("cheapest_price")) or Decimal("999999999999"),
            str(row["origin"]),
        ))
        return payload

    def _configured_origins(self) -> tuple[Origin, ...]:
        if self.config.origins:
            return self.config.origins
        grouped: dict[str, list[str]] = {}
        for route in self.config.routes:
            grouped.setdefault(route.origin, []).append(route.destination)
        return tuple(
            Origin(code=code, name=code, destinations=tuple(destinations), enabled=True)
            for code, destinations in grouped.items()
        )

    def _origin_name(self, code: str) -> str:
        code = code.upper()
        for origin in self._configured_origins():
            if origin.code == code:
                return origin.name
        return code

def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _scan_run_payload(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    current = None
    if d.get("status") == "running" and d.get("current_origin") and d.get("current_destination"):
        current = {
            "origin": d.get("current_origin"), "destination": d.get("current_destination"),
            "departure_date": d.get("current_departure_date"), "return_date": d.get("current_return_date"),
        }
    return {
        "id": d.get("id"), "provider": d.get("provider"),
        "provider_label": _provider_label(str(d.get("provider") or "")),
        "status": d.get("status"),
        "limit_searches": d.get("limit_searches"), "total_searches": d.get("total_searches"),
        "searches": d.get("searches"), "quotes": d.get("quotes"),
        "alerts": d.get("alerts"), "failures": d.get("failures"),
        "current": current, "error": d.get("error"),
        "started_at": d.get("started_at"), "updated_at": d.get("updated_at"),
        "completed_at": d.get("completed_at"),
    }


def _scan_failure_payload(row: Any) -> dict[str, Any]:
    d = dict(row)
    return {
        "id": d.get("id"), "scan_run_id": d.get("scan_run_id"),
        "origin": d.get("origin"), "destination": d.get("destination"),
        "departure_date": d.get("departure_date"), "return_date": d.get("return_date"),
        "provider": d.get("provider"),
        "provider_label": (_provider_label(str(d.get("provider") or "")) if d.get("provider") else None),
        "error": d.get("error"), "created_at": d.get("created_at"),
    }


def _route_key(origin: str, destination: str) -> str:
    return f"{origin.upper()}-{destination.upper()}"


def _route_has_custom_threshold(route: Any, origin: Origin) -> bool:
    return (
        route.max_price != origin.default_max_price
        or route.mistake_fare_below != origin.default_mistake_fare_below
    )


def _provider_label(provider: str) -> str:
    if "," in provider:
        return ", ".join(_provider_label(p.strip()) for p in provider.split(",") if p.strip())
    labels = {
        "google_flights_structured": "Google Flights", "google_flights": "Google Flights",
        "fast_flights": "Google Flights", "flight_finder_scraper": "Legacy",
        "google_flights_scraper": "Legacy", "skyscanner": "Skyscanner", "mock": "Mock",
    }
    return labels.get(provider, provider)


def _scan_status(searches: int, failures: int) -> str:
    if failures <= 0:
        return "success"
    if searches > failures:
        return "partial"
    return "failed"


def _classify_row(row: dict[str, Any], route: Any) -> dict[str, Any]:
    """Clasificación determinística sin score mágico. Reglas (en orden):
    ``max_price``/``mistake_fare_below`` → ``deal``; nuevo mínimo histórico
    → ``deal``; si no, ``normal``. ``mistake`` queda reservado para alertas
    persistidas o descuentos contra mediana histórica."""
    price = _decimal_or_none(row.get("price"))
    if price is None:
        return _empty_classification("Sin precio", "Sin precio usable")
    threshold_delta: Decimal | None = None
    historical_delta: Decimal | None = None
    historical_delta_pct: Decimal | None = None
    note = "Precio normal"
    status_kind, status_label, score = "normal", "Normal", Decimal("0")
    if route and route.mistake_fare_below is not None and price <= route.mistake_fare_below:
        threshold_delta = route.mistake_fare_below - price
        status_kind, status_label, score = "deal", "Buen precio", Decimal("50")
        note = f"{_format_decimal(threshold_delta)} bajo umbral fuerte"
    elif route and route.max_price is not None and price <= route.max_price:
        threshold_delta = route.max_price - price
        status_kind, status_label, score = "deal", "Buen precio", Decimal("50")
        note = f"{_format_decimal(threshold_delta)} bajo umbral"
    else:
        min_price = _decimal_or_none(row.get("route_min_price"))
        if min_price is not None and min_price > 0:
            historical_delta = min_price - price
            historical_delta_pct = (historical_delta / min_price) * Decimal("100")
            if price <= min_price:
                status_kind, status_label, score = "deal", "Buen precio", Decimal("20")
                note = "Nuevo mínimo observado"
    return {
        "status_kind": status_kind, "status_label": status_label,
        "opportunity_score": str(score.quantize(Decimal("1"))),
        "threshold_delta": _qd(threshold_delta, Decimal("1")),
        "historical_delta": _qd(historical_delta, Decimal("1")),
        "historical_delta_pct": _qd(historical_delta_pct, Decimal("0.1")),
        "opportunity_note": note,
    }


def _empty_classification(label: str, note: str) -> dict[str, Any]:
    return {
        "status_kind": "normal", "status_label": label, "opportunity_score": "0",
        "threshold_delta": None, "historical_delta": None, "historical_delta_pct": None,
        "opportunity_note": note,
    }


def _best_composite_origin_offer(
    origin: str,
    destination: str,
    rows_by_route: dict[str, list[dict[str, Any]]],
    route_stats: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    origin = origin.upper()
    destination = destination.upper()
    if origin == destination:
        return None
    best: dict[str, Any] | None = None
    for key, first_legs in rows_by_route.items():
        first_origin, _, hub = key.partition("-")
        if first_origin != origin or hub == destination:
            continue
        second_legs = rows_by_route.get(_route_key(hub, destination), [])
        for first in first_legs:
            first_price = _decimal_or_none(first.get("price"))
            if first_price is None:
                continue
            for second in second_legs:
                second_price = _decimal_or_none(second.get("price"))
                if second_price is None:
                    continue
                total = first_price + second_price
                first_stats = route_stats.get(_route_key(origin, hub), {})
                second_stats = route_stats.get(_route_key(hub, destination), {})
                candidate = {
                    "price": str(total.quantize(Decimal("1"))),
                    "carrier": f"via {hub}",
                    "observed_at": max(str(first.get("observed_at") or ""), str(second.get("observed_at") or "")),
                    "booking_url": second.get("booking_url") or first.get("booking_url"),
                    "samples": int(first_stats.get("samples") or 0) + int(second_stats.get("samples") or 0),
                    "route_min_price": _sum_optional_decimals(
                        first_stats.get("min_price"),
                        second_stats.get("min_price"),
                    ),
                    "via": hub,
                }
                if best is None or total < (_decimal_or_none(best.get("price")) or Decimal("999999999999")):
                    best = candidate
    return best


def _sum_optional_decimals(a: Any, b: Any) -> str | None:
    left = _decimal_or_none(a)
    right = _decimal_or_none(b)
    if left is None or right is None:
        return None
    return str((left + right).quantize(Decimal("1")))


def _quote_type(row: dict[str, Any]) -> str:
    severity = str(row.get("severity") or "").strip()
    if severity == "mistake_fare":
        return "mistake_fare"
    if severity == "deal":
        return "deal"
    if row.get("status_kind") == "mistake":
        return "mistake_fare"
    if row.get("status_kind") == "deal":
        return "deal"
    return "baseline"


def _quote_type_label(quote_type: str) -> str:
    return {
        "mistake_fare": "Mistake fare",
        "deal": "Deal",
        "baseline": "Baseline",
    }.get(quote_type, "Baseline")


def _qd(value: Decimal | None, quantum: Decimal) -> str | None:
    """Quantize a Decimal and return its string form, or ``None``."""
    if value is None:
        return None
    return str(value.quantize(quantum))


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _format_decimal(value: Decimal) -> str:
    return f"{int(value.quantize(Decimal('1'))):,}".replace(",", ".")


def _route_offer_sort_key(row: dict[str, Any]) -> tuple[Decimal, Decimal, str]:
    score = _decimal_or_none(row.get("opportunity_score")) or Decimal("0")
    price = _decimal_or_none(row.get("price")) or Decimal("999999999999")
    observed_at = str(row.get("observed_at") or "")
    return (-score, price, observed_at)
