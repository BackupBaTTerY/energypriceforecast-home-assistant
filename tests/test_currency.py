"""Tests for asking the API for a market's own currency.

The API converts from euro at the ECB's daily reference rate; the integration
only has to ask. What matters here is when it asks: only when the option is on
and the market has a currency to switch to. Everything else must produce the
exact request it produced before the option existed - Denmark and Norway are
answered in DKK and NOK precisely because nothing is asked.
"""
from __future__ import annotations

import pytest

from custom_components.energypriceforecast.api import (
    EnergyPriceForecastApi,
    requested_currency,
)


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type: str | None = None) -> dict:
        return self._payload


class _CapturingSession:
    """Serves one payload and records the query parameters of every request."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.params: list[dict] = []

    def get(self, url: str, params=None, headers=None, timeout=None) -> _Response:
        self.params.append(dict(params or {}))
        return _Response(self._payload)


def _summary(country: str) -> dict:
    return {
        "format": "home-assistant-summary",
        "country": country,
        "flat": {},
        "meta": {"api_key_state": "missing"},
    }


def _prices(country: str) -> dict:
    return {"format": "home-assistant-prices", "country": country, "entries": []}


def _api(session, market: str, currency: str | None = None) -> EnergyPriceForecastApi:
    return EnergyPriceForecastApi(
        session=session,
        base_url="https://example.invalid/summary",
        prices_url="https://example.invalid/prices",
        market=market,
        horizon_hours=48,
        window_hours=4,
        currency=currency,
    )


async def test_a_configured_currency_is_asked_for_on_both_endpoints() -> None:
    """Summary and price series must agree, or the sensors would mix currencies."""
    summary = _CapturingSession(_summary("CZ"))
    await _api(summary, "CZ", "CZK").async_get_summary()
    prices = _CapturingSession(_prices("CZ"))
    await _api(prices, "CZ", "CZK").async_get_prices(price_mode="base")

    assert summary.params[0]["currency"] == "CZK"
    assert prices.params[0]["currency"] == "CZK"


async def test_without_a_currency_nothing_is_asked() -> None:
    """No parameter at all - not currency=EUR, which would move DK and NO to euro."""
    summary = _CapturingSession(_summary("DK1"))
    await _api(summary, "DK1").async_get_summary()
    prices = _CapturingSession(_prices("DK1"))
    await _api(prices, "DK1").async_get_prices(price_mode="base")

    assert "currency" not in summary.params[0]
    assert "currency" not in prices.params[0]


@pytest.mark.parametrize(
    ("market", "enabled", "expected"),
    [
        ("CZ", True, "CZK"),
        ("cz", True, "CZK"),
        ("PL", True, "PLN"),
        ("SE1", True, "SEK"),
        ("SE4", True, "SEK"),
        ("CZ", False, None),
        # Euro markets have nothing to switch to.
        ("DE", True, None),
        # Denmark and Norway already get their own currency unasked. A value
        # here would put a currency into their plan keys, and every stored
        # plan would be re-picked on upgrade.
        ("DK1", True, None),
        ("NO3", True, None),
        # The API does not offer francs, so Switzerland stays in euro.
        ("CH", True, None),
    ],
)
def test_requested_currency(market: str, enabled: bool, expected: str | None) -> None:
    assert requested_currency(market, enabled) == expected
