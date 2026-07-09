from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .scanner import FlightScanner, ScanStats


@dataclass(frozen=True)
class WatchOptions:
    interval_minutes: float
    jitter_seconds: float = 0
    cycles: int | None = None
    limit_searches: int | None = None


class WatchRunner:
    def __init__(
        self,
        scanner: FlightScanner,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        rand: Callable[[float, float], float] = random.uniform,
        output: Callable[[str], None] = print,
    ) -> None:
        self.scanner = scanner
        self.sleeper = sleeper
        self.now = now
        self.rand = rand
        self.output = output

    def run(self, options: WatchOptions) -> list[ScanStats]:
        if options.interval_minutes <= 0:
            raise ValueError("interval_minutes must be greater than zero.")
        if options.jitter_seconds < 0:
            raise ValueError("jitter_seconds cannot be negative.")

        completed: list[ScanStats] = []
        cycle = 0
        while options.cycles is None or cycle < options.cycles:
            cycle += 1
            started_at = self.now().isoformat(timespec="seconds")
            try:
                stats = self.scanner.run_once(limit_searches=options.limit_searches)
                completed.append(stats)
                self.output(
                    "watch cycle complete: "
                    f"cycle={cycle} started_at={started_at} "
                    f"searches={stats.searches} quotes={stats.quotes} "
                    f"alerts={stats.alerts} failures={stats.failures}"
                )
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                self.output(f"watch cycle failed: cycle={cycle} started_at={started_at} error={exc}")

            if options.cycles is not None and cycle >= options.cycles:
                break

            self.sleeper(self._sleep_seconds(options))

        return completed

    def _sleep_seconds(self, options: WatchOptions) -> float:
        base = options.interval_minutes * 60
        if options.jitter_seconds == 0:
            return base
        return max(0, base + self.rand(-options.jitter_seconds, options.jitter_seconds))
