from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from .config import load_config
from .dashboard import serve_dashboard
from .notify import Notifier
from .providers import build_providers
from .runner import WatchOptions, WatchRunner
from .scanner import FlightScanner
from .storage import FareStore

DEFAULT_CONFIG = Path("config/faresnipe.toml")
EXAMPLE_CONFIG = Path("config/origins.example.toml")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="faresnipe",
        description="Open-source mistake fare hunter. Snipe cheap flights before airlines fix them.",
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to the faresnipe TOML config.")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Create config/faresnipe.toml from the example.")
    init_p.add_argument("--from", dest="origin", help="Keep only one origin, for example: SCL.")
    init_p.add_argument("--to", dest="destinations", help="Comma-separated destinations, for example: EZE,MAD,YYZ.")

    run_p = sub.add_parser("run", help="Run a scan. Use --watch for continuous.")
    run_p.add_argument("--watch", action="store_true", help="Run continuously on a schedule.")
    run_p.add_argument("--once", action="store_true", help="Run one scan and exit. This is the default.")
    run_p.add_argument("--limit", type=int, default=None, help="Limit the number of searches.")
    run_p.add_argument("--dry-run", action="store_true", help="Use the mock provider.")
    run_p.add_argument(
        "--provider",
        choices=["google_flights_structured", "mock", "skyscanner"],
        help="Provider to use for this scan.",
    )

    serve_p = sub.add_parser("serve", help="Start the local dashboard.")
    serve_p.add_argument("--host", default="127.0.0.1", help="Host interface to bind.")
    serve_p.add_argument("--port", type=int, default=8765, help="Port to listen on.")

    args = parser.parse_args(argv)

    if args.command == "init":
        destinations = _parse_destinations(args.destinations)
        if destinations and not args.origin:
            parser.error("init --to requires --from")
        init_config(Path(args.config), args.origin, destinations)
        return

    config = load_config(args.config)
    if args.command == "serve":
        serve_dashboard(config=config, host=args.host, port=args.port)
        return
    if args.once and args.watch:
        parser.error("run --once cannot be combined with --watch")

    provider_names = _provider_names(config.scanner.provider_names)
    if args.provider:
        provider_names = [args.provider]
    if args.dry_run:
        provider_names = ["mock"]
    config = replace(
        config,
        scanner=replace(config.scanner, provider=provider_names[0], providers=tuple(provider_names)),
    )

    store = FareStore(config.scanner.database_path)
    scanner = FlightScanner(
        config=config,
        providers=build_providers(tuple(provider_names)),
        store=store,
        notifier=Notifier(
            console=config.notifications.console,
            webhook_url=config.notifications.webhook_url,
            telegram_bot_token=config.notifications.telegram_bot_token,
            telegram_chat_id=config.notifications.telegram_chat_id,
        ),
    )
    if args.watch:
        WatchRunner(scanner).run(
            WatchOptions(
                interval_minutes=config.scanner.scan_interval_minutes,
                jitter_seconds=config.scanner.scan_jitter_seconds,
                limit_searches=args.limit,
            )
        )
        return

    stats = scanner.run_once(limit_searches=args.limit)
    print(
        "scan complete: "
        f"searches={stats.searches} quotes={stats.quotes} "
        f"alerts={stats.alerts} failures={stats.failures}"
    )


def init_config(
    dest: Path = DEFAULT_CONFIG,
    origin: str | None = None,
    destinations: tuple[str, ...] = (),
) -> None:
    if dest.exists():
        print(f"{dest} already exists; leaving it untouched.")
        return
    try:
        if origin:
            _write_origin_config(dest, origin.upper(), destinations)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_example_config_path(), dest)
    except OSError as exc:
        print(f"Could not create {dest}: {exc}")
        return
    print(f"Created {dest}")
    print("Edit routes and thresholds in that file.")
    print("Then run `faresnipe run --once --limit 5` for a first scan.")
    print("Use `faresnipe serve` to open the dashboard.")


def _write_origin_config(dest: Path, origin: str, destinations: tuple[str, ...] = ()) -> None:
    import tomllib

    example_path = _example_config_path()
    data = tomllib.loads(example_path.read_text(encoding="utf-8"))
    origins = [item for item in data.get("origins", []) if str(item.get("code", "")).upper() == origin]
    if not origins:
        raise OSError(f"origin {origin} not found in {example_path}")
    destination_filter = set(destinations)
    if destination_filter:
        for item in origins:
            item["destinations"] = [
                str(destination).upper()
                for destination in item.get("destinations", [])
                if str(destination).upper() in destination_filter
            ]
        missing = destination_filter - {
            destination
            for item in origins
            for destination in item.get("destinations", [])
        }
        if missing:
            raise OSError(f"destination(s) not configured for {origin}: {', '.join(sorted(missing))}")
    thresholds = [
        item for item in data.get("route_thresholds", [])
        if str(item.get("origin", "")).upper() == origin
        and (not destination_filter or str(item.get("destination", "")).upper() in destination_filter)
    ]
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[scanner]",
        'provider = "google_flights_structured"',
        'database_path = "data/faresnipe.sqlite3"',
        f'currency = "{str(data.get("scanner", {}).get("currency", "CLP")).upper()}"',
        "days_ahead_start = 7",
        "days_ahead_end = 30",
        "stay_lengths = [7, 14]",
        "adults = 1",
        "max_results_per_search = 8",
        "request_delay_seconds = 0.2",
        "scan_interval_minutes = 30",
        "scan_jitter_seconds = 60",
        "",
        "[detection]",
        "discount_ratio = 0.35",
        "mistake_fare_ratio = 0.55",
        "min_history_quotes = 4",
        "history_days = 180",
        "",
        "[notifications]",
        "console = true",
        'webhook_url = ""',
        'telegram_bot_token = ""',
        'telegram_chat_id = ""',
        "",
    ]
    for item in origins:
        lines.extend([
            "[[origins]]",
            f'code = "{origin}"',
            f'name = "{item.get("name", origin)}"',
            "destinations = [" + ", ".join(f'"{d}"' for d in item.get("destinations", [])) + "]",
            f"default_max_price = {item.get('default_max_price', 0)}",
            f"default_mistake_fare_below = {item.get('default_mistake_fare_below', 0)}",
            "enabled = true",
            "",
        ])
    for item in thresholds:
        lines.extend([
            "[[route_thresholds]]",
            f'origin = "{origin}"',
            f'destination = "{item["destination"]}"',
            f"max_price = {item['max_price']}",
            f"mistake_fare_below = {item['mistake_fare_below']}",
            "",
        ])
    dest.write_text("\n".join(lines), encoding="utf-8")


def _parse_destinations(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    destinations = [item.strip().upper() for item in value.split(",") if item.strip()]
    return tuple(dict.fromkeys(destinations))


def _example_config_path() -> Path:
    if EXAMPLE_CONFIG.exists():
        return EXAMPLE_CONFIG
    source_checkout_example = Path(__file__).resolve().parents[2] / EXAMPLE_CONFIG
    if source_checkout_example.exists():
        return source_checkout_example
    return EXAMPLE_CONFIG


def _provider_names(values) -> list[str]:  # type: ignore[no-untyped-def]
    names = [str(value).strip().lower() for value in values if str(value).strip()]
    return list(dict.fromkeys(names)) or ["google_flights_structured"]


if __name__ == "__main__":
    main(sys.argv[1:])
