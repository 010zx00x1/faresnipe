from __future__ import annotations

import unittest

from faresnipe.runner import WatchOptions, WatchRunner
from faresnipe.scanner import ScanStats


class FakeScanner:
    def __init__(self) -> None:
        self.calls = 0
        self.limits = []

    def run_once(self, limit_searches=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.limits.append(limit_searches)
        return ScanStats(searches=2, quotes=4, alerts=1)


class WatchRunnerTest(unittest.TestCase):
    def test_run_stops_after_configured_cycles_and_sleeps_between_cycles(self) -> None:
        scanner = FakeScanner()
        sleeps = []
        messages = []
        runner = WatchRunner(
            scanner=scanner,  # type: ignore[arg-type]
            sleeper=sleeps.append,
            rand=lambda start, end: 5,
            output=messages.append,
        )

        stats = runner.run(
            WatchOptions(
                interval_minutes=10,
                jitter_seconds=5,
                cycles=2,
                limit_searches=7,
            )
        )

        self.assertEqual(scanner.calls, 2)
        self.assertEqual(scanner.limits, [7, 7])
        self.assertEqual(len(stats), 2)
        self.assertEqual(sleeps, [605])
        self.assertEqual(len(messages), 2)

    def test_rejects_non_positive_interval(self) -> None:
        runner = WatchRunner(scanner=FakeScanner())  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            runner.run(WatchOptions(interval_minutes=0, cycles=1))


if __name__ == "__main__":
    unittest.main()
