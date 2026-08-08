"""Tests for diagnostics redaction."""
from __future__ import annotations

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energypriceforecast.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_api_key_and_postal_code_are_redacted(hass) -> None:
    """The stored API key and postal code must never appear in diagnostics."""
    entry = MockConfigEntry(
        domain="energypriceforecast",
        data={
            "market": "DE",
            "horizon_hours": 48,
            "window_hours": 4,
            "api_key": "super-secret-key",
            "retail_pricing": True,
            "postal_code": "10115",
        },
    )
    entry.runtime_data = SimpleNamespace(
        data={"flat": {"current_price": 0.2}},
        retail_data=None,
        cheapest_hours=None,
        last_update_success=True,
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["config"]["api_key"] == "**REDACTED**"
    assert diagnostics["config"]["postal_code"] == "**REDACTED**"
    assert diagnostics["config"]["market"] == "DE"
    assert diagnostics["data"] == {"flat": {"current_price": 0.2}}
    assert diagnostics["last_update_success"] is True


async def test_diagnostics_include_retail_and_cheapest_hours_state(hass) -> None:
    """Diagnostics should reflect the optional retail/cheapest-hours features."""
    entry = MockConfigEntry(
        domain="energypriceforecast",
        data={"market": "DE", "horizon_hours": 48, "window_hours": 4},
    )
    entry.runtime_data = SimpleNamespace(
        data={},
        retail_data={"entries": []},
        cheapest_hours=[{"average_value": 0.1}],
        last_update_success=True,
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["retail_data"] == {"entries": []}
    assert diagnostics["cheapest_hours"] == [{"average_value": 0.1}]
