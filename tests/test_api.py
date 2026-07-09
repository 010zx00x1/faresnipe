from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from faresnipe.api import app
from faresnipe.models import FareQuote
from faresnipe.storage import FareStore


def _seed_store(path) -> None:  # type: ignore[no-untyped-def]
    store = FareStore(path)
    observed_at = datetime.now(timezone.utc)
    store.save_quote(
        FareQuote(
            provider="test",
            origin="SCL",
            destination="EZE",
            departure_date=date(2026, 8, 15),
            return_date=date(2026, 8, 22),
            price=Decimal("437"),
            currency="USD",
            carrier="JetSMART",
            booking_url="https://www.google.com/travel/flights?q=SCL-EZE",
            observed_at=observed_at,
        )
    )
    for index, (destination, price) in enumerate(
        [("LIM", "199"), ("BOG", "250"), ("MIA", "599")]
    ):
        departure = date.today() + timedelta(days=7 + index)
        store.save_quote(
            FareQuote(
                provider="test",
                origin="SCL",
                destination=destination,
                departure_date=departure,
                return_date=departure + timedelta(days=7),
                price=Decimal(price),
                currency="USD",
                carrier="Test Air",
                booking_url=f"https://www.google.com/travel/flights?q=SCL-{destination}",
                observed_at=observed_at,
            )
        )


def test_healthcheck_returns_200() -> None:
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_route_date_returns_json_from_existing_data(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "faresnipe.sqlite3"
    _seed_store(db_path)
    monkeypatch.setenv("FARESNIPE_DATABASE", str(db_path))
    client = TestClient(app)

    response = client.get("/api/SCL/EZE/2026-08-15")

    assert response.status_code == 200
    payload = response.json()
    assert payload["origin"] == "SCL"
    assert payload["destination"] == "EZE"
    assert payload["date"] == "2026-08-15"
    assert payload["price"] == 437
    assert payload["currency"] == "USD"
    assert payload["carrier"] == "JetSMART"
    assert payload["fresh"] is True


def test_anywhere_returns_list(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "faresnipe.sqlite3"
    _seed_store(db_path)
    monkeypatch.setenv("FARESNIPE_DATABASE", str(db_path))
    client = TestClient(app)

    response = client.get("/api/SCL/anywhere")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) >= 3
    assert payload[0]["origin"] == "SCL"
    assert payload[0]["destination"] == "LIM"


def test_text_plain_accept_returns_readable_text(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "faresnipe.sqlite3"
    _seed_store(db_path)
    monkeypatch.setenv("FARESNIPE_DATABASE", str(db_path))
    client = TestClient(app)

    response = client.get("/api/SCL/EZE/2026-08-15", headers={"Accept": "text/plain"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "SCL -> EZE  2026-08-15" in response.text
    assert "$437 USD  JetSMART" in response.text
    assert "https://www.google.com/travel/flights" in response.text


def test_unknown_route_returns_404(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "faresnipe.sqlite3"
    _seed_store(db_path)
    monkeypatch.setenv("FARESNIPE_DATABASE", str(db_path))
    client = TestClient(app)

    response = client.get("/api/AAA/BBB")

    assert response.status_code == 404
    assert response.json() == {"error": "no data", "origin": "AAA", "destination": "BBB"}
