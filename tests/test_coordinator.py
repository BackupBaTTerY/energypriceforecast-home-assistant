"""Tests for the coordinator's cheapest-hours and weekend plan locking.

Once a plan has been picked for a block, it must survive later forecast
updates unchanged - see planning.py for why a reshuffled plan would defeat a
recurring automation. These tests exercise that lock through full config
entry setup and refresh cycles, the same way test_sensor.py does, so the
Store-backed cache in coordinator.py is exercised for real rather than
mocked away.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "energypriceforecast"

SUMMARY_PAYLOAD = {
    "api_version": "v1",
    "format": "home-assistant-summary",
    "generated_at": "2026-08-12T00:00:00Z",
    "country": "DE",
    "flat": {
        "current_price": 0.21,
        "current_price_unit": "EUR/kWh",
        "current_co2_g_kwh": 320.5,
        "best_price_window_avg_price": 0.15,
        "best_price_window_start": "2026-08-12T04:00:00Z",
        "best_price_window_end": "2026-08-12T08:00:00Z",
        "best_co2_window_avg_g_co2_kwh": 250.0,
        "best_co2_window_start": "2026-08-12T04:00:00Z",
        "best_co2_window_end": "2026-08-12T08:00:00Z",
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


def _full_day_entries(start_iso: str, prices_by_hour: dict[int, float]) -> list[dict]:
    """Quarter-hour entries covering every hour in prices_by_hour, from start_iso."""
    from datetime import datetime, timedelta

    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    entries = []
    for hour_offset, price in prices_by_hour.items():
        for quarter in range(4):
            slot_start = start + timedelta(hours=hour_offset, minutes=quarter * 15)
            entries.append(
                {
                    "start": slot_start.isoformat(),
                    "end": (slot_start + timedelta(minutes=15)).isoformat(),
                    "value": price,
                }
            )
    return entries


async def _setup_entry(hass, extra_data: dict | None = None, price_entries=None):
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
        "entries": price_entries if price_entries is not None else [],
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


async def _refresh_with_prices(hass, entry, price_entries) -> None:
    prices_payload = {
        "format": "home-assistant-prices",
        "country": "DE",
        "currency": "EUR",
        "unit": "EUR/kWh",
        "entries": price_entries,
    }
    with patch(
        "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
        ".async_get_prices",
        new=AsyncMock(return_value=prices_payload),
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()


async def test_cheapest_hours_plan_locks_and_survives_a_reshuffled_forecast(
    hass, freezer
) -> None:
    """A later forecast update must not change an already-picked block plan."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T10:00:00+00:00")

    # Only hours from "now" (10:00) onward are ever eligible for selection,
    # so the cheap hours picked here must fall within 10:00-23:59.
    initial_entries = _full_day_entries(
        "2026-08-12T00:00:00Z", {h: 0.50 for h in range(24)} | {14: 0.05, 16: 0.10}
    )
    entry = await _setup_entry(
        hass,
        extra_data={"cheapest_hours_count": 2, "cheapest_hours_window_hours": 24},
        price_entries=initial_entries,
    )

    first_plan = entry.runtime_data.cheapest_hours
    assert first_plan is not None
    assert [h["start"].hour for h in first_plan] == [14, 16]

    # The forecast now claims very different hours are cheapest - the
    # already-published plan must not move to follow it.
    reshuffled_entries = _full_day_entries(
        "2026-08-12T00:00:00Z", {h: 0.50 for h in range(24)} | {20: 0.01, 22: 0.02}
    )
    await _refresh_with_prices(hass, entry, reshuffled_entries)

    second_plan = entry.runtime_data.cheapest_hours
    assert [h["start"].hour for h in second_plan] == [14, 16]


async def test_no_plan_is_published_when_the_block_is_not_fully_covered(
    hass, freezer
) -> None:
    """An incomplete forecast must yield no plan at all, never a partial one."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T10:00:00+00:00")

    # Only a few hours of the 24-hour block have data.
    partial_entries = _full_day_entries("2026-08-12T00:00:00Z", {11: 0.10, 12: 0.20})
    entry = await _setup_entry(
        hass,
        extra_data={"cheapest_hours_count": 2, "cheapest_hours_window_hours": 24},
        price_entries=partial_entries,
    )

    assert entry.runtime_data.cheapest_hours is None


async def test_weekend_plan_requires_the_full_window_including_past_hours(
    hass, freezer
) -> None:
    """The weekend plan only locks in once the entire Sat-Mon window is covered."""
    await hass.config.async_set_time_zone("UTC")
    # A Saturday, already a few hours in - the weekend block started at 00:00.
    freezer.move_to("2026-08-15T10:00:00+00:00")

    # Missing coverage for hours 0-9 (already passed) must still block the plan.
    partial_entries = _full_day_entries(
        "2026-08-15T00:00:00Z", {h: 0.10 for h in range(10, 48)}
    )
    entry = await _setup_entry(
        hass,
        extra_data={"weekend_hours_count": 4},
        price_entries=partial_entries,
    )
    assert entry.runtime_data.weekend_hours is None

    full_entries = _full_day_entries(
        "2026-08-15T00:00:00Z",
        {h: 0.50 for h in range(48)} | {2: 0.01, 3: 0.02, 4: 0.03, 5: 0.04},
    )
    await _refresh_with_prices(hass, entry, full_entries)

    plan = entry.runtime_data.weekend_hours
    assert plan is not None
    assert [h["start"].hour for h in plan] == [2, 3, 4, 5]


async def test_weekend_plan_is_independent_of_the_block_plan(hass, freezer) -> None:
    """Each plan only sees its own window, so they can pick different hours."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-15T00:00:00+00:00")

    # Cheapest within the first 24h (hour 1) differs from the cheapest across
    # the full 48h weekend window (hour 30, on day two).
    entries = _full_day_entries(
        "2026-08-15T00:00:00Z",
        {h: 0.50 for h in range(48)} | {1: 0.02, 30: 0.01},
    )
    entry = await _setup_entry(
        hass,
        extra_data={
            "cheapest_hours_count": 1,
            "cheapest_hours_window_hours": 24,
            "weekend_hours_count": 1,
        },
        price_entries=entries,
    )

    assert [h["start"].hour for h in entry.runtime_data.cheapest_hours] == [1]
    assert [h["start"].hour for h in entry.runtime_data.weekend_hours] == [6]
