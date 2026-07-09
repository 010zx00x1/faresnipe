from __future__ import annotations

import json
import unittest
from datetime import date
from decimal import Decimal

from faresnipe.models import Baseline, DealAlert, FareQuote
from faresnipe.notify import Notifier, format_alert


class DummyResponse:
    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, exc_type, exc, traceback):  # type: ignore[no-untyped-def]
        return False


def _alert() -> DealAlert:
    quote = FareQuote(
        provider="test",
        origin="SCL",
        destination="MAD",
        departure_date=date(2026, 9, 1),
        return_date=date(2026, 9, 15),
        price=Decimal("399"),
        currency="USD",
        carrier="XX",
    )
    return DealAlert(
        quote=quote,
        reasons=("price <= mistake threshold",),
        severity="mistake_fare",
        baseline=Baseline(median_price=Decimal("900"), min_price=Decimal("750"), quote_count=8),
        discount_ratio=Decimal("0.556"),
    )


class NotifierTest(unittest.TestCase):
    def test_send_posts_telegram_message_when_configured(self) -> None:
        calls = []

        def opener(request, timeout):  # type: ignore[no-untyped-def]
            calls.append((request, timeout))
            return DummyResponse()

        notifier = Notifier(
            console=False,
            telegram_bot_token="token",
            telegram_chat_id="chat-id",
            opener=opener,
        )

        notifier.send(_alert())

        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(timeout, 20)
        self.assertEqual(request.full_url, "https://api.telegram.org/bottoken/sendMessage")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["chat_id"], "chat-id")
        self.assertIn("SCL-MAD", body["text"])
        self.assertIn("399 USD", body["text"])

    def test_format_alert_handles_threshold_without_baseline(self) -> None:
        quote = FareQuote(
            provider="test",
            origin="AEP",
            destination="SCL",
            departure_date=date(2026, 9, 1),
            return_date=date(2026, 9, 8),
            price=Decimal("49"),
            currency="USD",
            carrier="XX",
        )
        alert = DealAlert(
            quote=quote,
            reasons=("price <= route max",),
            severity="deal",
            baseline=Baseline(median_price=None, min_price=None, quote_count=0),
            discount_ratio=None,
        )

        text = format_alert(alert)

        self.assertIn("AEP-SCL", text)
        self.assertIn("49 USD", text)
        self.assertIn("no baseline yet", text)

    # ---- Channel isolation: each channel can fail without breaking the rest. ----

    def test_webhook_failure_does_not_prevent_telegram_send(self) -> None:
        """If the webhook fails, the scan must not abort: Telegram should
        still receive the alert. This locks in channel-isolation behavior."""
        sent: list[str] = []

        def opener(request, timeout):  # type: ignore[no-untyped-def]
            sent.append(request.full_url)
            if "127.0.0.1:1" in request.full_url:
                raise ConnectionError("Connection refused")
            return DummyResponse()

        errors: list[str] = []
        notifier = Notifier(
            console=False,
            webhook_url="http://127.0.0.1:1/dead",
            telegram_bot_token="token",
            telegram_chat_id="chat-id",
            opener=opener,
            error_sink=errors.append,
        )

        # Must not raise.
        notifier.send(_alert())

        self.assertEqual(len(sent), 2, "both channels should have been tried")
        self.assertTrue(any("telegram.org" in u for u in sent))
        self.assertTrue(any("127.0.0.1:1" in u for u in sent))
        self.assertEqual(len(errors), 1)
        self.assertIn("webhook channel failed", errors[0])
        self.assertIn("Connection refused", errors[0])

    def test_telegram_failure_does_not_prevent_webhook_send(self) -> None:
        sent: list[str] = []

        def opener(request, timeout):  # type: ignore[no-untyped-def]
            sent.append(request.full_url)
            if "telegram.org" in request.full_url:
                raise ConnectionError("telegram api down")
            return DummyResponse()

        errors: list[str] = []
        notifier = Notifier(
            console=False,
            webhook_url="http://hook.example.com/x",
            telegram_bot_token="token",
            telegram_chat_id="chat-id",
            opener=opener,
            error_sink=errors.append,
        )

        notifier.send(_alert())

        self.assertEqual(len(sent), 2)
        self.assertTrue(any("hook.example.com" in u for u in sent))
        self.assertEqual(len(errors), 1)
        self.assertIn("telegram channel failed", errors[0])

    def test_all_channels_failing_does_not_raise(self) -> None:
        """If all channels fail, send() must still return cleanly because
        the scanner is iterating through quotes and cannot stop."""

        def opener(request, timeout):  # type: ignore[no-untyped-def]
            raise ConnectionError("network down")

        errors: list[str] = []
        notifier = Notifier(
            console=False,
            webhook_url="http://hook.example.com/x",
            telegram_bot_token="token",
            telegram_chat_id="chat-id",
            opener=opener,
            error_sink=errors.append,
        )

        # The only tolerable exception would be KeyboardInterrupt; any channel
        # exception must be captured in errors.
        notifier.send(_alert())

        self.assertEqual(len(errors), 2)
        self.assertTrue(any("webhook channel failed" in e for e in errors))
        self.assertTrue(any("telegram channel failed" in e for e in errors))

    def test_unconfigured_channels_are_not_called(self) -> None:
        """Without webhook or Telegram, send() must not make requests."""
        sent: list[str] = []

        def opener(request, timeout):  # type: ignore[no-untyped-def]
            sent.append(request.full_url)
            return DummyResponse()

        import contextlib
        import io

        notifier = Notifier(console=True, opener=opener)
        with contextlib.redirect_stdout(io.StringIO()):
            notifier.send(_alert())

        self.assertEqual(sent, [])

    def test_default_error_sink_writes_to_stderr(self) -> None:
        """Without a custom error_sink, channel failures must go to stderr,
        not pollute stdout where scanner output would go."""
        import io
        import contextlib

        notifier = Notifier(
            console=False,
            webhook_url="http://127.0.0.1:1/x",
            telegram_bot_token="t",
            telegram_chat_id="c",
        )
        # Replace the opener with one that always fails.
        notifier.opener = lambda req, timeout: (_ for _ in ()).throw(ConnectionError("net"))

        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            notifier.send(_alert())

        self.assertEqual(captured_out.getvalue(), "")
        self.assertIn("faresnipe notify: webhook channel failed", captured_err.getvalue())
        self.assertIn("faresnipe notify: telegram channel failed", captured_err.getvalue())

    def test_send_returns_none(self) -> None:
        """send() must return None and raise nothing except KeyboardInterrupt /
        SystemExit. This keeps scanner.run_once from having to wrap each
        notifier.send()."""
        import contextlib
        import io

        notifier = Notifier(console=True)
        with contextlib.redirect_stdout(io.StringIO()):
            result = notifier.send(_alert())
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
