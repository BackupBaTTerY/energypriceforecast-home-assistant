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

import pytest

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


def _full_day_entries(
    start_iso: str, prices_by_hour: dict[int, float], source: str | None = None
) -> list[dict]:
    """Quarter-hour entries covering every hour in prices_by_hour, from start_iso.

    Without a source the entries read as a forecast, which is what most of
    these tests want: a plan built on one is the only kind the day-ahead
    auction is ever allowed to revise.
    """
    from datetime import datetime, timedelta

    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    entries = []
    for hour_offset, price in prices_by_hour.items():
        for quarter in range(4):
            slot_start = start + timedelta(hours=hour_offset, minutes=quarter * 15)
            entry = {
                "start": slot_start.isoformat(),
                "end": (slot_start + timedelta(minutes=15)).isoformat(),
                "value": price,
            }
            if source is not None:
                entry["source"] = source
            entries.append(entry)
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
    """Force a coordinator refresh with new price data.

    Must also mock async_get_summary - without it, the refresh's real
    (unmocked) summary call fails with a network error, _async_update_data
    raises UpdateFailed before ever reaching the plan recomputation, and
    a test asserting the plan is unchanged would pass for the wrong
    reason (the code never ran) rather than because the lock held.
    """
    prices_payload = {
        "format": "home-assistant-prices",
        "country": "DE",
        "currency": "EUR",
        "unit": "EUR/kWh",
        "entries": price_entries,
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
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert entry.runtime_data.last_update_success, (
        "refresh failed - a real network call likely slipped through an "
        "incomplete mock"
    )


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


async def test_a_forecast_plan_is_repicked_once_when_the_day_ahead_arrives(
    hass, freezer
) -> None:
    """The auction may correct a guess - but only that once.

    Locking exists to keep forecast churn from reshuffling the picks, not to
    defend a guess against the published price. So the first day-ahead that
    covers the rest of the block re-picks the hours that are still ahead,
    and every update after that leaves them alone.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T10:00:00+00:00")

    forecast_entries = _full_day_entries(
        "2026-08-12T00:00:00Z", {h: 0.50 for h in range(24)} | {14: 0.05, 16: 0.10}
    )
    entry = await _setup_entry(
        hass,
        extra_data={"cheapest_hours_count": 2, "cheapest_hours_window_hours": 24},
        price_entries=forecast_entries,
    )
    assert [h["start"].hour for h in entry.runtime_data.cheapest_hours] == [14, 16]

    # The auction publishes, and the real cheapest hours are elsewhere.
    freezer.move_to("2026-08-12T10:30:00+00:00")
    settled_entries = _full_day_entries(
        "2026-08-12T00:00:00Z",
        {h: 0.50 for h in range(24)} | {20: 0.01, 22: 0.02},
        source="day_ahead",
    )
    await _refresh_with_prices(hass, entry, settled_entries)
    assert [h["start"].hour for h in entry.runtime_data.cheapest_hours] == [20, 22]

    # Anything after that must not move it again, published or not.
    freezer.move_to("2026-08-12T11:00:00+00:00")
    later_entries = _full_day_entries(
        "2026-08-12T00:00:00Z",
        {h: 0.50 for h in range(24)} | {19: 0.001, 21: 0.002},
        source="day_ahead",
    )
    await _refresh_with_prices(hass, entry, later_entries)
    assert [h["start"].hour for h in entry.runtime_data.cheapest_hours] == [20, 22]


async def test_a_plan_built_on_published_prices_is_never_repicked(
    hass, freezer
) -> None:
    """Nothing better is coming, so the plan is settled the moment it is made."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T10:00:00+00:00")

    settled_entries = _full_day_entries(
        "2026-08-12T00:00:00Z",
        {h: 0.50 for h in range(24)} | {14: 0.05, 16: 0.10},
        source="day_ahead",
    )
    entry = await _setup_entry(
        hass,
        extra_data={"cheapest_hours_count": 2, "cheapest_hours_window_hours": 24},
        price_entries=settled_entries,
    )
    assert [h["start"].hour for h in entry.runtime_data.cheapest_hours] == [14, 16]

    freezer.move_to("2026-08-12T10:30:00+00:00")
    reshuffled = _full_day_entries(
        "2026-08-12T00:00:00Z",
        {h: 0.50 for h in range(24)} | {20: 0.01, 22: 0.02},
        source="day_ahead",
    )
    await _refresh_with_prices(hass, entry, reshuffled)

    assert [h["start"].hour for h in entry.runtime_data.cheapest_hours] == [14, 16]


async def test_an_hour_that_has_already_started_is_never_repicked(
    hass, freezer
) -> None:
    """A running automation must not be pulled out from under itself."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T10:00:00+00:00")

    # The hour the plan is locked in is one of the two cheapest.
    forecast_entries = _full_day_entries(
        "2026-08-12T00:00:00Z", {h: 0.50 for h in range(24)} | {10: 0.05, 14: 0.10}
    )
    entry = await _setup_entry(
        hass,
        extra_data={"cheapest_hours_count": 2, "cheapest_hours_window_hours": 24},
        price_entries=forecast_entries,
    )
    assert [h["start"].hour for h in entry.runtime_data.cheapest_hours] == [10, 14]

    # Half an hour later the auction says hours 20 and 21 are the cheap ones.
    freezer.move_to("2026-08-12T10:30:00+00:00")
    settled_entries = _full_day_entries(
        "2026-08-12T00:00:00Z",
        {h: 0.50 for h in range(24)} | {20: 0.01, 21: 0.02},
        source="day_ahead",
    )
    await _refresh_with_prices(hass, entry, settled_entries)

    plan = entry.runtime_data.cheapest_hours
    # The hour under way keeps its place and its slot in the count, so only
    # one hour is re-picked - not both.
    assert [h["start"].hour for h in plan] == [10, 20]


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


def _co2_summary(start_iso: str, values_by_hour: dict[int, float]) -> dict:
    """A summary payload carrying the CO2 series, as include_series returns it."""
    from datetime import datetime, timedelta

    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    return {
        **SUMMARY_PAYLOAD,
        "co2": {"available": True, "unit": "gCO2/kWh"},
        "series": {
            "co2": [
                {
                    "start": (start + timedelta(hours=hour))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "end": (start + timedelta(hours=hour + 1))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "value": value,
                    "unit": "gCO2/kWh",
                    "source": None,
                }
                for hour, value in sorted(values_by_hour.items())
            ]
        },
    }


async def _setup_with_summary(hass, extra_data: dict, summary: dict):
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
            **extra_data,
        },
    )
    entry.add_to_hass(hass)
    prices_payload = {
        "format": "home-assistant-prices",
        "country": "DE",
        "currency": "EUR",
        "unit": "EUR/kWh",
        "entries": [],
    }
    with (
        patch(
            "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
            ".async_get_summary",
            new=AsyncMock(return_value=summary),
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


async def test_greenest_plan_is_picked_from_the_co2_series(hass, freezer) -> None:
    """The CO2 plan ranks emissions, and never touches the price series."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T00:00:00+00:00")

    values = {hour: 400.0 for hour in range(24)}
    values.update({6: 80.0, 9: 120.0})

    entry = await _setup_with_summary(
        hass,
        {"greenest_hours_count": 2, "cheapest_hours_window_hours": 24},
        _co2_summary("2026-08-12T00:00:00Z", values),
    )

    plan = entry.runtime_data.greenest_hours
    assert [hour["start"].hour for hour in plan] == [6, 9]
    # Priced in CO2, not in the price series' currency.
    assert entry.runtime_data.co2_series["unit"] == "gCO2/kWh"
    # The price plan is untouched: it was never switched on here.
    assert entry.runtime_data.cheapest_hours is None


async def test_greenest_plan_needs_the_whole_block_covered(hass, freezer) -> None:
    """A partial CO2 forecast yields no plan at all, as on the price side."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T00:00:00+00:00")

    entry = await _setup_with_summary(
        hass,
        {"greenest_hours_count": 2, "cheapest_hours_window_hours": 24},
        _co2_summary("2026-08-12T00:00:00Z", {0: 100.0, 1: 200.0}),
    )

    assert entry.runtime_data.greenest_hours is None


async def test_no_co2_series_means_no_co2_plan(hass, freezer) -> None:
    """A market without CO2 coverage must not produce an empty or stale plan."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T00:00:00+00:00")

    entry = await _setup_with_summary(
        hass,
        {"greenest_hours_count": 2, "cheapest_hours_window_hours": 24},
        SUMMARY_PAYLOAD,
    )

    assert entry.runtime_data.co2_series is None
    assert entry.runtime_data.greenest_hours is None


async def test_a_plan_without_a_currency_keeps_its_old_key(hass, freezer) -> None:
    """Upgrading must still find every plan stored before the option existed.

    The currency only enters the key when one is asked for. A key that changed
    for everyone would lose each current block's plan on upgrade and re-pick it
    halfway through - the one thing a locked plan promises never to do.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T10:00:00+00:00")
    entry = await _setup_entry(
        hass,
        extra_data={"cheapest_hours_count": 2, "cheapest_hours_window_hours": 24},
        price_entries=_full_day_entries(
            "2026-08-12T00:00:00Z",
            {h: 0.50 for h in range(24)} | {14: 0.05, 16: 0.10},
        ),
    )

    keys = list(entry.runtime_data._plan_cache)
    assert len(keys) == 1
    assert keys[0].startswith("block|base|2|24|0|")


async def test_switching_currency_starts_a_fresh_plan(hass, freezer) -> None:
    """A euro plan must not be shown next to koruna prices after a switch.

    Reconfiguring reloads the entry, but the stored plans live on disk and
    survive the reload. With the currency outside the key, the reloaded entry
    would find this block's euro plan and keep serving its euro averages.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-12T10:00:00+00:00")
    rate = 24.25
    euro = {h: 0.50 for h in range(24)} | {14: 0.05, 16: 0.10}
    entry = await _setup_entry(
        hass,
        extra_data={
            "market": "CZ",
            "cheapest_hours_count": 2,
            "cheapest_hours_window_hours": 24,
        },
        price_entries=_full_day_entries("2026-08-12T00:00:00Z", euro),
    )
    assert entry.runtime_data.currency is None
    euro_average = entry.runtime_data.cheapest_hours_window_average
    assert euro_average is not None

    koruna_payload = {
        "format": "home-assistant-prices",
        "country": "CZ",
        "currency": "CZK",
        "unit": "CZK/kWh",
        "entries": _full_day_entries(
            "2026-08-12T00:00:00Z", {h: p * rate for h, p in euro.items()}
        ),
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
            new=AsyncMock(return_value=koruna_payload),
        ),
    ):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "local_currency": True}
        )
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    assert coordinator.currency == "CZK"
    # The same hours - one conversion rate keeps their order ...
    assert [h["start"].hour for h in coordinator.cheapest_hours] == [14, 16]
    # ... but priced in koruna rather than carried over from the euro plan.
    assert coordinator.cheapest_hours_window_average == pytest.approx(
        euro_average * rate
    )
