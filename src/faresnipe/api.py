from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .config import AppConfig, load_config
from .models import Route
from .notify import Notifier
from .providers import build_providers
from .scanner import FlightScanner
from .storage import FareStore

IATA_RE = re.compile(r"^[A-Z]{3}$")
FRESH_WINDOW = timedelta(hours=6)
SCAN_WAIT_SECONDS = 10
DEFAULT_CONFIG = Path("config/faresnipe.toml")

app = FastAPI(title="faresnipe API")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/{origin}/{destination}/{departure_date}")
async def best_for_date(
    origin: str,
    destination: str,
    departure_date: str,
    accept: str | None = Header(default=None),
) -> Response:
    route_origin = _iata(origin, "origin")
    route_destination = _iata(destination, "destination")
    try:
        parsed_date = date.fromisoformat(departure_date)
    except ValueError as exc:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail="date must be YYYY-MM-DD") from exc
    return await _route_response(route_origin, route_destination, parsed_date, accept)


@app.get("/api/{origin}/anywhere")
async def best_anywhere(origin: str, accept: str | None = Header(default=None)) -> Response:
    route_origin = _iata(origin, "origin")
    return await _anywhere_response(route_origin, accept)


@app.get("/api/{origin}/{destination}")
async def best_for_route(
    origin: str,
    destination: str,
    accept: str | None = Header(default=None),
) -> Response:
    route_origin = _iata(origin, "origin")
    if destination.lower() == "anywhere":
        return await _anywhere_response(route_origin, accept)
    route_destination = _iata(destination, "destination")
    return await _route_response(route_origin, route_destination, None, accept)


async def _route_response(
    origin: str,
    destination: str,
    departure_date: date | None,
    accept: str | None,
) -> Response:
    config = _load_app_config()
    store = FareStore(config.scanner.database_path)
    row = _best_route_row(store, origin, destination, departure_date)
    if row and _is_fresh(row):
        return _format_response(_row_payload(row, fresh=True), accept)

    if not _can_scan_route(config, origin, destination):
        return _no_data(origin, destination, accept)

    scan_result = await _scan_and_wait(config, store, origin, destination, departure_date)
    if scan_result == "pending":
        return _scanning(origin, destination, accept)

    row = _best_route_row(store, origin, destination, departure_date)
    if row:
        return _format_response(_row_payload(row, fresh=_is_fresh(row)), accept)
    return _no_data(origin, destination, accept)


async def _anywhere_response(origin: str, accept: str | None) -> Response:
    config = _load_app_config()
    store = FareStore(config.scanner.database_path)
    rows = _best_anywhere_rows(store, origin)
    if rows and all(_is_fresh(row) for row in rows):
        return _format_response([_row_payload(row, fresh=_is_fresh(row)) for row in rows], accept)

    destinations = _configured_destinations(config, origin)
    if not destinations:
        return _no_data(origin, "anywhere", accept)

    scan_result = await _scan_and_wait(config, store, origin, None, None)
    if scan_result == "pending":
        return _scanning(origin, "anywhere", accept)

    rows = _best_anywhere_rows(store, origin)
    if rows:
        return _format_response([_row_payload(row, fresh=_is_fresh(row)) for row in rows], accept)
    return _no_data(origin, "anywhere", accept)


def _load_app_config() -> AppConfig:
    return load_config(DEFAULT_CONFIG)


def _best_route_row(
    store: FareStore,
    origin: str,
    destination: str,
    departure_date: date | None,
) -> dict[str, Any] | None:
    params: list[Any] = [origin, destination]
    date_filter = ""
    if departure_date is not None:
        date_filter = "AND departure_date = ?"
        params.append(departure_date.isoformat())
    else:
        today = date.today().isoformat()
        horizon = (date.today() + timedelta(days=30)).isoformat()
        row = _fetch_one(
            store,
            f"""
            SELECT * FROM fares
            WHERE origin = ? AND destination = ?
              AND departure_date BETWEEN ? AND ?
            ORDER BY CAST(price AS REAL) ASC, observed_at DESC
            LIMIT 1
            """,
            [origin, destination, today, horizon],
        )
        if row is not None:
            return row
    return _fetch_one(
        store,
        f"""
        SELECT * FROM fares
        WHERE origin = ? AND destination = ? {date_filter}
        ORDER BY CAST(price AS REAL) ASC, observed_at DESC
        LIMIT 1
        """,
        params,
    )


def _best_anywhere_rows(store: FareStore, origin: str) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    horizon = (date.today() + timedelta(days=30)).isoformat()
    rows = _fetch_all(
        store,
        """
        WITH ranked AS (
          SELECT *,
                 ROW_NUMBER() OVER (
                   PARTITION BY destination
                   ORDER BY CAST(price AS REAL) ASC, observed_at DESC
                 ) AS rn
          FROM fares
          WHERE origin = ? AND departure_date BETWEEN ? AND ?
        )
        SELECT * FROM ranked WHERE rn = 1
        ORDER BY CAST(price AS REAL) ASC LIMIT 5
        """,
        [origin, today, horizon],
    )
    if rows:
        return rows
    return _fetch_all(
        store,
        """
        WITH ranked AS (
          SELECT *,
                 ROW_NUMBER() OVER (
                   PARTITION BY destination
                   ORDER BY CAST(price AS REAL) ASC, observed_at DESC
                 ) AS rn
          FROM fares
          WHERE origin = ?
        )
        SELECT * FROM ranked WHERE rn = 1
        ORDER BY CAST(price AS REAL) ASC LIMIT 5
        """,
        [origin],
    )


def _fetch_one(store: FareStore, query: str, params: list[Any]) -> dict[str, Any] | None:
    rows = _fetch_all(store, query, params)
    return rows[0] if rows else None


def _fetch_all(store: FareStore, query: str, params: list[Any]) -> list[dict[str, Any]]:
    with store._connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


async def _scan_and_wait(
    config: AppConfig,
    store: FareStore,
    origin: str,
    destination: str | None,
    departure_date: date | None,
) -> str:
    lock = _scan_lock()
    if lock.locked():
        return "pending"
    task = asyncio.create_task(
        run_in_threadpool(_run_scan_once, config, store, origin, destination, departure_date)
    )
    done, _pending = await asyncio.wait({task}, timeout=SCAN_WAIT_SECONDS)
    if task in done:
        task.result()
        return "done"
    return "pending"


def _run_scan_once(
    config: AppConfig,
    store: FareStore,
    origin: str,
    destination: str | None,
    departure_date: date | None,
) -> None:
    lock = _scan_lock()
    if not lock.acquire(blocking=False):
        return
    try:
        routes = _scan_routes(config, origin, destination)
        if not routes:
            return
        scanner_config = config.scanner
        if departure_date is not None:
            offset = (departure_date - date.today()).days
            if offset < 0:
                return
            scanner_config = replace(
                scanner_config,
                days_ahead_start=offset,
                days_ahead_end=offset,
            )
        scan_config = replace(config, scanner=scanner_config, routes=tuple(routes))
        providers = build_providers(scan_config.scanner.provider_names)
        scanner = FlightScanner(
            config=scan_config,
            providers=providers,
            store=store,
            notifier=Notifier(console=False),
        )
        scanner.run_once()
    finally:
        lock.release()


def _scan_lock() -> threading.Lock:
    lock = getattr(app.state, "scan_lock", None)
    if lock is None:
        lock = threading.Lock()
        app.state.scan_lock = lock
    return lock


def _scan_routes(config: AppConfig, origin: str, destination: str | None) -> list[Route]:
    if destination is not None:
        return [
            route for route in config.routes
            if route.enabled and route.origin == origin and route.destination == destination
        ]
    return [route for route in config.routes if route.enabled and route.origin == origin]


def _can_scan_route(config: AppConfig, origin: str, destination: str) -> bool:
    return bool(_scan_routes(config, origin, destination))


def _configured_destinations(config: AppConfig, origin: str) -> list[str]:
    return [route.destination for route in config.routes if route.enabled and route.origin == origin]


def _row_payload(row: dict[str, Any], fresh: bool) -> dict[str, Any]:
    return {
        "origin": row["origin"],
        "destination": row["destination"],
        "date": row["departure_date"],
        "price": _json_price(row["price"]),
        "currency": row["currency"],
        "carrier": row.get("carrier"),
        "booking_url": row.get("booking_url"),
        "scanned_at": row["observed_at"],
        "fresh": fresh,
    }


def _json_price(value: Any) -> int | float:
    price = Decimal(str(value))
    if price == price.to_integral_value():
        return int(price)
    return float(price)


def _is_fresh(row: dict[str, Any]) -> bool:
    observed = _parse_datetime(str(row["observed_at"]))
    return datetime.now(timezone.utc) - observed <= FRESH_WINDOW


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_response(payload: dict[str, Any] | list[dict[str, Any]], accept: str | None) -> Response:
    if _wants_text(accept):
        rows = payload if isinstance(payload, list) else [payload]
        return PlainTextResponse("\n\n".join(_text_payload(row) for row in rows))
    return JSONResponse(payload)


def _text_payload(row: dict[str, Any]) -> str:
    carrier = row.get("carrier") or "Unknown carrier"
    booking_url = row.get("booking_url") or ""
    return (
        f"{row['origin']} -> {row['destination']}  {row['date']}\n"
        f"${row['price']} {row['currency']}  {carrier}\n"
        f"{booking_url}"
    )


def _no_data(origin: str, destination: str, accept: str | None) -> Response:
    payload = {"error": "no data", "origin": origin, "destination": destination}
    if _wants_text(accept):
        return PlainTextResponse(
            f"no data for {origin} -> {destination}",
            status_code=HTTPStatus.NOT_FOUND,
        )
    return JSONResponse(payload, status_code=HTTPStatus.NOT_FOUND)


def _scanning(origin: str, destination: str, accept: str | None) -> Response:
    payload = {
        "status": "scanning",
        "message": "scanning, try again in 30s",
        "origin": origin,
        "destination": destination,
    }
    if _wants_text(accept):
        return PlainTextResponse(payload["message"], status_code=HTTPStatus.ACCEPTED)
    return JSONResponse(payload, status_code=HTTPStatus.ACCEPTED)


def _wants_text(accept: str | None) -> bool:
    return bool(accept and "text/plain" in accept.lower())


def _iata(value: str, field: str) -> str:
    code = value.upper()
    if not IATA_RE.match(code):
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST, detail=f"{field} must be a 3-letter IATA code")
    return code
