from __future__ import annotations

import os
import tomllib
import warnings
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import Origin, Route


@dataclass(frozen=True)
class ScannerConfig:
    provider: str
    database_path: Path
    currency: str
    days_ahead_start: int
    days_ahead_end: int
    stay_lengths: tuple[int, ...]
    adults: int
    max_results_per_search: int
    request_delay_seconds: float
    scan_interval_minutes: float
    scan_jitter_seconds: float
    providers: tuple[str, ...] = ()

    @property
    def provider_names(self) -> tuple[str, ...]:
        return self.providers or (self.provider,)


@dataclass(frozen=True)
class DetectionConfig:
    discount_ratio: Decimal
    mistake_fare_ratio: Decimal
    min_history_quotes: int
    history_days: int


@dataclass(frozen=True)
class NotificationConfig:
    console: bool
    webhook_url: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None


@dataclass(frozen=True)
class AppConfig:
    scanner: ScannerConfig
    detection: DetectionConfig
    notifications: NotificationConfig
    routes: tuple[Route, ...]
    origins: tuple[Origin, ...] = ()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    scanner_data = data.get("scanner", {})
    detection_data = data.get("detection", {})
    notification_data = data.get("notifications", {})

    origins = tuple(_parse_origin(item) for item in data.get("origins", []))
    threshold_map = _parse_route_thresholds(data.get("route_thresholds", []))
    routes = _expand_origin_routes(origins, threshold_map)
    if not routes and data.get("routes"):
        warnings.warn(
            "[[routes]] is deprecated; use [[origins]] plus [[route_thresholds]] instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        routes = tuple(_parse_route(item) for item in data.get("routes", []))
        origins = _origins_from_routes(routes)
    if not routes:
        raise ValueError("Config must define at least one enabled route through [[origins]] or [[routes]].")

    database_path = Path(
        os.environ.get(
            "FARESNIPE_DATABASE",
            str(scanner_data.get("database_path", "data/faresnipe.sqlite3")),
        )
    )

    providers = _parse_providers(scanner_data)

    return AppConfig(
        scanner=ScannerConfig(
            provider=providers[0],
            providers=providers,
            database_path=database_path,
            currency=str(scanner_data.get("currency", "USD")).upper(),
            days_ahead_start=int(scanner_data.get("days_ahead_start", 7)),
            days_ahead_end=int(scanner_data.get("days_ahead_end", 120)),
            stay_lengths=tuple(int(v) for v in scanner_data.get("stay_lengths", [7, 14])),
            adults=int(scanner_data.get("adults", 1)),
            max_results_per_search=int(scanner_data.get("max_results_per_search", 8)),
            request_delay_seconds=float(scanner_data.get("request_delay_seconds", 0.3)),
            scan_interval_minutes=float(scanner_data.get("scan_interval_minutes", 30)),
            scan_jitter_seconds=float(scanner_data.get("scan_jitter_seconds", 60)),
        ),
        detection=DetectionConfig(
            discount_ratio=_decimal(detection_data.get("discount_ratio", "0.35")),
            mistake_fare_ratio=_decimal(detection_data.get("mistake_fare_ratio", "0.55")),
            min_history_quotes=int(detection_data.get("min_history_quotes", 4)),
            history_days=int(detection_data.get("history_days", 180)),
        ),
        notifications=NotificationConfig(
            console=bool(notification_data.get("console", True)),
            webhook_url=str(notification_data.get("webhook_url") or "").strip() or None,
            telegram_bot_token=str(
                os.environ.get(
                    "FARESNIPE_TELEGRAM_BOT_TOKEN",
                    notification_data.get("telegram_bot_token", ""),
                )
                or ""
            ).strip()
            or None,
            telegram_chat_id=str(
                os.environ.get(
                    "FARESNIPE_TELEGRAM_CHAT_ID",
                    notification_data.get("telegram_chat_id", ""),
                )
                or ""
            ).strip()
            or None,
        ),
        routes=routes,
        origins=origins,
    )


def _parse_origin(data: dict[str, Any]) -> Origin:
    destinations = data.get("destinations", [])
    if isinstance(destinations, str):
        destinations = [destinations]
    return Origin(
        code=str(data["code"]).upper(),
        name=str(data.get("name") or data["code"]),
        destinations=tuple(str(destination).upper() for destination in destinations),
        default_max_price=_optional_decimal(data.get("default_max_price")),
        default_mistake_fare_below=_optional_decimal(data.get("default_mistake_fare_below")),
        enabled=bool(data.get("enabled", True)),
    )


def _parse_route(data: dict[str, Any]) -> Route:
    return Route(
        origin=str(data["origin"]).upper(),
        destination=str(data["destination"]).upper(),
        enabled=bool(data.get("enabled", True)),
        max_price=_optional_decimal(data.get("max_price")),
        mistake_fare_below=_optional_decimal(data.get("mistake_fare_below")),
    )


def _parse_route_thresholds(items: list[dict[str, Any]]) -> dict[tuple[str, str], tuple[Decimal | None, Decimal | None]]:
    thresholds: dict[tuple[str, str], tuple[Decimal | None, Decimal | None]] = {}
    for item in items:
        key = (str(item["origin"]).upper(), str(item["destination"]).upper())
        thresholds[key] = (
            _optional_decimal(item.get("max_price")),
            _optional_decimal(item.get("mistake_fare_below")),
        )
    return thresholds


def _expand_origin_routes(
    origins: tuple[Origin, ...],
    threshold_map: dict[tuple[str, str], tuple[Decimal | None, Decimal | None]],
) -> tuple[Route, ...]:
    routes: list[Route] = []
    seen: set[tuple[str, str]] = set()
    for origin in origins:
        if not origin.enabled:
            continue
        for destination in origin.destinations:
            max_price = origin.default_max_price
            mistake_fare_below = origin.default_mistake_fare_below
            override = threshold_map.get((origin.code, destination))
            if override is not None:
                max_price, mistake_fare_below = override
            routes.append(
                Route(
                    origin=origin.code,
                    destination=destination,
                    enabled=True,
                    max_price=max_price,
                    mistake_fare_below=mistake_fare_below,
                )
            )
            seen.add((origin.code, destination))
    origin_lookup = {origin.code: origin for origin in origins if origin.enabled}
    for (origin_code, destination), (max_price, mistake_fare_below) in threshold_map.items():
        if (origin_code, destination) in seen or origin_code not in origin_lookup:
            continue
        routes.append(
            Route(
                origin=origin_code,
                destination=destination,
                enabled=True,
                max_price=max_price,
                mistake_fare_below=mistake_fare_below,
            )
        )
    return tuple(routes)


def _origins_from_routes(routes: tuple[Route, ...]) -> tuple[Origin, ...]:
    grouped: dict[str, list[str]] = {}
    for route in routes:
        grouped.setdefault(route.origin, []).append(route.destination)
    return tuple(
        Origin(code=code, name=code, destinations=tuple(destinations), enabled=True)
        for code, destinations in grouped.items()
    )


def _parse_providers(scanner_data: dict[str, Any]) -> tuple[str, ...]:
    env_providers = os.environ.get("FARESNIPE_PROVIDERS")
    if env_providers:
        raw: Any = env_providers.split(",")
    elif "providers" in scanner_data:
        raw = scanner_data["providers"]
    else:
        raw = [os.environ.get("FARESNIPE_PROVIDER", scanner_data.get("provider", "google_flights_structured"))]

    if isinstance(raw, str):
        raw = [raw]

    providers: list[str] = []
    for value in raw:
        provider = str(value).strip().lower()
        if provider and provider not in providers:
            providers.append(provider)
    if not providers:
        providers.append("mock")
    return tuple(providers)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return _decimal(value)


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))
