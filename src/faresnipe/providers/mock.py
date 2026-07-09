from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from faresnipe.models import FareQuote, SearchQuery


# Tabla de conversion desde "unidades base" (interpretadas como USD
# a la hora de inventar numeros) hacia la moneda que pida el query.
# Aproximacion: 1 USD ~= 950 CLP, EUR ~= 1.08 USD, etc.
_USD_TO_CURRENCY = {
    "USD": Decimal("1"),
    "CLP": Decimal("950"),
    "EUR": Decimal("0.92"),
    "BRL": Decimal("5.0"),
    "ARS": Decimal("900"),
    "PEN": Decimal("3.7"),
    "COP": Decimal("4000"),
    "MXN": Decimal("17"),
    "GBP": Decimal("0.79"),
}


class MockFlightProvider:
    name = "mock"

    def search(self, query: SearchQuery, max_results: int) -> list[FareQuote]:
        # Precios base "como si" estuvieran en USD.
        base = self._base_price(query.origin, query.destination)
        day_factor = Decimal((query.departure_date.toordinal() % 19) - 8)
        stay_factor = Decimal(0)
        if query.return_date:
            stay_factor = Decimal((query.return_date - query.departure_date).days) * Decimal("1.7")

        digest = hashlib.sha256(
            f"{query.origin}-{query.destination}-{query.departure_date}-{query.return_date}".encode()
        ).hexdigest()
        jitter = Decimal(int(digest[:4], 16) % 55)
        price_usd = base + day_factor + stay_factor + jitter

        if digest.endswith(("00", "11", "22")):
            price_usd *= Decimal("0.42")
        elif digest.endswith(("33", "44", "55", "66")):
            price_usd *= Decimal("0.68")

        rate = _USD_TO_CURRENCY.get(query.currency, Decimal("1"))
        # Para monedas con magnitud grande (CLP, ARS, COP) redondeamos a
        # unidades enteras; para el resto, dos decimales.
        if rate >= Decimal("100"):
            quant = Decimal("1")
        else:
            quant = Decimal("0.01")
        price_in_currency = (price_usd * rate).quantize(quant)

        quotes: list[FareQuote] = []
        for index in range(min(max_results, 3)):
            quote_price = price_in_currency + Decimal(index * 18) * rate
            quote_price = quote_price.quantize(quant)
            quotes.append(
                FareQuote(
                    provider=self.name,
                    origin=query.origin,
                    destination=query.destination,
                    departure_date=query.departure_date,
                    return_date=query.return_date,
                    price=quote_price,
                    currency=query.currency,
                    carrier="MOCK",
                    flight_numbers=(f"PB{100 + index}",),
                    booking_url=None,
                    observed_at=datetime.now(timezone.utc),
                    raw={"synthetic": True, "base_usd": str(price_usd)},
                )
            )
        return quotes

    def _base_price(self, origin: str, destination: str) -> Decimal:
        # Precios "base" en USD; arriba se convierten a la currency del query.
        route = f"{origin}-{destination}"
        route_prices = {
            "SCL-EZE": Decimal("230"),
            "SCL-LIM": Decimal("260"),
            "SCL-GRU": Decimal("280"),
            "SCL-BOG": Decimal("310"),
            "SCL-MIA": Decimal("620"),
            "SCL-CUN": Decimal("640"),
            "SCL-JFK": Decimal("700"),
            "SCL-MEX": Decimal("560"),
            "SCL-MAD": Decimal("890"),
            "SCL-CDG": Decimal("950"),
            "SCL-FCO": Decimal("950"),
            "SCL-LHR": Decimal("980"),
            "AEP-SCL": Decimal("175"),
            "AEP-COR": Decimal("390"),
            "AEP-EZE": Decimal("360"),
            "AEP-MIA": Decimal("870"),
        }
        return route_prices.get(route, Decimal("420"))


def search(
    origin: str,
    destination: str,
    depart_range,
    return_range,
    stay_lengths: tuple[int, ...],
    adults: int,
    currency: str,
) -> list[FareQuote]:
    query = SearchQuery(
        origin=origin,
        destination=destination,
        departure_date=depart_range,
        return_date=return_range,
        adults=adults,
        currency=currency,
    )
    return MockFlightProvider().search(query, 8)
