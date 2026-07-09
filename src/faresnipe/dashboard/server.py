"""HTTP dashboard server for faresnipe (stdlib, no frameworks)."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..config import AppConfig
from .state import DashboardServer

STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def serve_dashboard(config: AppConfig, host: str, port: int) -> None:
    state = DashboardServer(config=config)
    handler = _build_handler(state)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"dashboard listening on http://{host}:{port}")
    server.serve_forever()


def _build_handler(state: DashboardServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/" or path == "/index.html":
                self._serve_file(INDEX_FILE, "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
                return
            if path == "/api/summary":
                self._send_json(state.summary()); return
            if path == "/api/recent":
                self._send_json({"rows": state.enrich_rows(_store_rows(
                    state.store.recent_quotes(
                        _limit_from_query(parsed.query),
                        currency=state.config.scanner.currency,
                        providers=state.config.scanner.provider_names,
                    )
                ))}); return
            if path == "/api/deals":
                self._send_json({"rows": _store_rows(state.store.cheapest_quotes(
                    _limit_from_query(parsed.query),
                    currency=state.config.scanner.currency,
                    providers=state.config.scanner.provider_names,
                ))}); return
            if path == "/api/opportunities":
                self._send_json({"rows": state.opportunities_payload()}); return
            if path == "/api/stats":
                self._send_json({"rows": _store_rows(state.store.route_stats(
                    currency=state.config.scanner.currency,
                    providers=state.config.scanner.provider_names,
                ))}); return
            if path == "/api/routes":
                self._send_json({"rows": state.configured_routes_payload()}); return
            if path == "/api/origins":
                self._send_json({"origins": state.origins_payload()}); return
            if path == "/api/compare-origins":
                params = parse_qs(parsed.query)
                destination = (params.get("destination") or [""])[0].strip().upper()
                if not destination:
                    self._send_json(
                        {"error": "destination is required"},
                        HTTPStatus.BAD_REQUEST,
                    ); return
                departure_date = (params.get("departure_date") or [None])[0] or None
                return_date = (params.get("return_date") or [None])[0] or None
                currency = (params.get("currency") or [None])[0] or None
                self._send_json({
                    "rows": state.compare_origins_payload(
                        destination=destination,
                        departure_date=departure_date,
                        return_date=return_date,
                        currency=currency,
                    ),
                    "destination": destination,
                }); return
            if path == "/api/config":
                self._send_json(state.config_payload()); return
            if path == "/api/history":
                params = parse_qs(parsed.query)
                origin = (params.get("origin") or [""])[0]
                destination = (params.get("destination") or [""])[0]
                if not origin or not destination:
                    self._send_json(
                        {"error": "origin and destination are required"},
                        HTTPStatus.BAD_REQUEST,
                    ); return
                self._send_json({"rows": state.enrich_rows(_store_rows(
                    state.store.price_history(
                        origin, destination, _limit_from_query(parsed.query),
                        currency=state.config.scanner.currency,
                        providers=state.config.scanner.provider_names,
                    )
                ))}); return
            if path == "/api/scan-runs":
                self._send_json({"rows": state.scan_runs_payload(
                    _limit_from_query(parsed.query)
                )}); return
            if path == "/api/scan-failures":
                params = parse_qs(parsed.query)
                limit = _limit_from_query(parsed.query)
                run_id = None
                if params.get("run_id"):
                    try:
                        run_id = int(params["run_id"][0])
                    except ValueError:
                        self._send_json(
                            {"error": "run_id must be an integer"},
                            HTTPStatus.BAD_REQUEST,
                        ); return
                self._send_json({"rows": state.scan_failures_payload(run_id, limit)}); return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/scan":
                body = self._read_json()
                try:
                    limit = int(body.get("limit_searches") or 1)
                except (TypeError, ValueError):
                    self._send_json(
                        {"error": "limit_searches must be an integer"},
                        HTTPStatus.BAD_REQUEST,
                    ); return
                providers = _providers_from_body(
                    body, state.config.scanner.provider_names
                )
                origin = str(body.get("origin") or "").strip().upper() or None
                result = state.run_scan(
                    limit_searches=limit, provider_names=providers, origin=origin
                )
                status = HTTPStatus.CONFLICT if "error" in result else HTTPStatus.OK
                self._send_json(result, status); return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return value if isinstance(value, dict) else {}

        def _send_json(self, body: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(body, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _serve_static(self, name: str) -> None:
            safe = _safe_static_path(name)
            if safe is None:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            ctype = _CONTENT_TYPES.get(safe.suffix.lower(), "application/octet-stream")
            self._serve_file(safe, ctype)

        def _serve_file(self, path: Path, content_type: str) -> None:
            try:
                body = path.read_bytes()
            except FileNotFoundError:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _safe_static_path(name: str) -> Path | None:
    """Resolve a path under ``STATIC_DIR`` without allowing path traversal."""
    clean = name.lstrip("/")
    if ".." in Path(clean).parts:
        return None
    candidate = (STATIC_DIR / clean).resolve()
    try:
        candidate.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _store_rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _providers_from_body(
    body: dict[str, Any], default: tuple[str, ...]
) -> tuple[str, ...]:
    raw = body.get("providers")
    if raw is None:
        raw = body.get("provider")
    if raw is None:
        return default
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    providers: list[str] = []
    for value in values:
        provider = str(value).strip().lower()
        if provider and provider not in providers:
            providers.append(provider)
    return tuple(providers or default)


def _limit_from_query(query: str) -> int:
    params = parse_qs(query)
    try:
        return max(1, min(500, int((params.get("limit") or ["50"])[0])))
    except ValueError:
        return 50
