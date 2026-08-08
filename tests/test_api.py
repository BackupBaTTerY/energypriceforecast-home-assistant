"""Tests for the API client's handling of a rejected API key.

The public API never returns HTTP 401/403 for a rejected key: it responds
with 200 and reports the outcome in meta.api_key_state instead. A caller
that only checked the HTTP status would treat a rejected key as success.
"""
from __future__ import annotations

import pytest

from custom_components.energypriceforecast.api import (
    REJECTED_API_KEY_STATES,
    EnergyPriceForecastApi,
    EnergyPriceForecastAuthError,
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status = 200

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    async def json(self, content_type: str | None = None) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def get(self, url: str, params=None, headers=None, timeout=None) -> _FakeResponse:
        return _FakeResponse(self._payload)


def _payload(api_key_state: str) -> dict:
    return {
        "format": "home-assistant-summary",
        "country": "NO3",
        "flat": {},
        "meta": {"api_key_state": api_key_state},
    }


async def test_missing_key_ignores_api_key_state() -> None:
    """Without a configured key, the api_key_state field is not enforced."""
    api = EnergyPriceForecastApi(
        session=_FakeSession(_payload("missing")),
        base_url="https://example.invalid",
        market="NO3",
        horizon_hours=48,
        window_hours=4,
        api_key=None,
    )
    payload = await api.async_get_summary()
    assert payload["meta"]["api_key_state"] == "missing"


async def test_valid_key_succeeds() -> None:
    """A key reported as valid does not raise."""
    api = EnergyPriceForecastApi(
        session=_FakeSession(_payload("valid")),
        base_url="https://example.invalid",
        market="NO3",
        horizon_hours=48,
        window_hours=4,
        api_key="a-real-looking-key-123456",
    )
    payload = await api.async_get_summary()
    assert payload["meta"]["api_key_state"] == "valid"


@pytest.mark.parametrize("state", sorted(REJECTED_API_KEY_STATES))
async def test_rejected_key_raises_auth_error(state: str) -> None:
    """Every non-valid api_key_state raises when a key was supplied."""
    api = EnergyPriceForecastApi(
        session=_FakeSession(_payload(state)),
        base_url="https://example.invalid",
        market="NO3",
        horizon_hours=48,
        window_hours=4,
        api_key="a-real-looking-key-123456",
    )
    with pytest.raises(EnergyPriceForecastAuthError):
        await api.async_get_summary()
