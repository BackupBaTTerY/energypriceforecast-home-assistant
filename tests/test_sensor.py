"""Integration tests for the sensor/binary_sensor platforms."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "energypriceforecast"

SUMMARY_PAYLOAD = {
    "api_version": "v1",
    "format": "home-assistant-summary",
    "generated_at": "2026-08-08T03:00:00Z",
    "country": "DE",
    "flat": {
        "current_price": 0.21,
        "current_price_unit": "EUR/kWh",
        "current_co2_g_kwh": 320.5,
        "best_price_window_avg_price": 0.15,
        "best_price_window_start": "2026-08-08T04:00:00Z",
        "best_price_window_end": "2026-08-08T08:00:00Z",
        "best_co2_window_avg_g_co2_kwh": 250.0,
        "best_co2_window_start": "2026-08-08T04:00:00Z",
        "best_co2_window_end": "2026-08-08T08:00:00Z",
        "combined_window_score": 0.2,
        "is_cheapest_window_now": False,
        "is_greenest_window_now": True,
    },
    "meta": {
        "allowed_horizon_hours": 48,
        "used_horizon_hours": 48,
        "api_key_state": "missing",
    },
}


async def _setup_entry(
    hass, extra_data: dict | None = None, price_entries: list[dict] | None = None
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DE",
        data={
            "market": "DE",
            "horizon_hours": 48,
            "window_hours": 4,
            "update_interval_minutes": 30,
            "cheapest_hours_count": 0,
            "retail_pricing": False,
            **(extra_data or {}),
        },
    )
    entry.add_to_hass(hass)

    prices_payload = {
        "format": "home-assistant-prices",
        "country": "DE",
        "currency": "EUR",
        "unit": "EUR/kWh",
        "entries": price_entries
        if price_entries is not None
        else [
            {"start": "2026-08-08T04:00:00Z", "end": "2026-08-08T05:00:00Z", "value": 0.1}
        ],
    }
    with (
        patch(
            "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
            ".async_get_summary",
            new=AsyncMock(return_value=SUMMARY_PAYLOAD),
        ),
        patch(
            "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
            ".async_get_prices",
            new=AsyncMock(return_value=prices_payload),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    return entry


def _state_for_unique_id(hass, entry: MockConfigEntry, unique_id_suffix: str):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{unique_id_suffix}"
    ) or registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_{unique_id_suffix}"
    )
    assert entity_id is not None, f"no entity registered for {unique_id_suffix}"
    return hass.states.get(entity_id)


async def test_core_sensors_are_created_without_optional_features(hass) -> None:
    """Only the always-on entities exist when retail/cheapest-hours are off."""
    entry = await _setup_entry(hass)

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entries}

    assert f"{entry.entry_id}_current_price" in unique_ids
    assert f"{entry.entry_id}_cheapest_window_active" in unique_ids
    assert f"{entry.entry_id}_price_series" in unique_ids
    assert f"{entry.entry_id}_retail_current_price" not in unique_ids
    assert f"{entry.entry_id}_cheapest_hours_next_start" not in unique_ids
    assert f"{entry.entry_id}_is_in_cheapest_hours" not in unique_ids


async def test_current_price_sensor_reflects_summary_value(hass) -> None:
    entry = await _setup_entry(hass)

    state = _state_for_unique_id(hass, entry, "current_price")

    assert state.state == "0.21"
    assert state.attributes["unit_of_measurement"] == "EUR/kWh"


async def test_greenest_window_binary_sensor_is_on(hass) -> None:
    entry = await _setup_entry(hass)

    state = _state_for_unique_id(hass, entry, "greenest_window_active")

    assert state.state == "on"


async def test_cheapest_window_binary_sensor_is_off(hass) -> None:
    entry = await _setup_entry(hass)

    state = _state_for_unique_id(hass, entry, "cheapest_window_active")

    assert state.state == "off"


async def test_price_series_sensor_splits_today_and_tomorrow(hass, freezer) -> None:
    """raw_today/raw_tomorrow only include entries matching the local calendar date."""
    # The test hass instance does not default to UTC, so the "local calendar
    # date" being tested here would otherwise depend on that default.
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T10:00:00+00:00")
    entries = [
        {"start": "2026-08-08T11:00:00Z", "end": "2026-08-08T12:00:00Z", "value": 0.11},
        {"start": "2026-08-08T23:00:00Z", "end": "2026-08-09T00:00:00Z", "value": 0.12},
        {"start": "2026-08-09T05:00:00Z", "end": "2026-08-09T06:00:00Z", "value": 0.13},
    ]

    entry = await _setup_entry(hass, price_entries=entries)

    state = _state_for_unique_id(hass, entry, "price_series")

    assert [item["value"] for item in state.attributes["raw_today"]] == [0.11, 0.12]
    assert [item["value"] for item in state.attributes["raw_tomorrow"]] == [0.13]


async def test_optional_entities_created_when_features_enabled(hass) -> None:
    """retail_current_price and cheapest-hours entities appear once enabled."""
    entry = await _setup_entry(
        hass,
        extra_data={
            "retail_pricing": True,
            "postal_code": "10115",
            "cheapest_hours_count": 3,
        },
    )

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entries}

    assert f"{entry.entry_id}_retail_current_price" in unique_ids
    assert f"{entry.entry_id}_cheapest_hours_next_start" in unique_ids
    assert f"{entry.entry_id}_is_in_cheapest_hours" in unique_ids
