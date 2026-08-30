"""Integration tests for the sensor/binary_sensor platforms."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energypriceforecast.sensor import (
    _day_statistics,
    _next_planned_start,
)

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
    hass,
    extra_data: dict | None = None,
    price_entries: list[dict] | None = None,
    retail_summary_payload: dict | None = None,
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

    async def _summary_side_effect(price_mode="base", postal_code=None):
        if price_mode == "retail":
            return retail_summary_payload or SUMMARY_PAYLOAD
        return SUMMARY_PAYLOAD

    with (
        patch(
            "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
            ".async_get_summary",
            new=AsyncMock(side_effect=_summary_side_effect),
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


def _plan(*hours: int) -> list[dict]:
    return [
        {
            "start": datetime(2026, 8, 29, hour, tzinfo=timezone.utc),
            "end": datetime(2026, 8, 29, hour + 1, tzinfo=timezone.utc),
            "average_value": 0.1,
        }
        for hour in hours
    ]


def test_next_planned_start_skips_hours_that_already_began(freezer) -> None:
    """The sensor must advance through the plan, not stick to its first entry.

    A plan covers its whole block, so returning hours[0] left a "next
    cheapest hour" sensor pointing further into the past with every hour
    that elapsed - it read "9 hours ago" by mid-block.
    """
    freezer.move_to("2026-08-29T12:00:00+00:00")

    assert _next_planned_start(_plan(8, 14, 20)) == datetime(
        2026, 8, 29, 14, tzinfo=timezone.utc
    )


def test_next_planned_start_is_none_once_the_plan_is_done(freezer) -> None:
    """All hours started: there is no next one, so report nothing."""
    freezer.move_to("2026-08-29T22:00:00+00:00")

    assert _next_planned_start(_plan(8, 14, 20)) is None


def test_next_planned_start_without_a_plan() -> None:
    assert _next_planned_start(None) is None
    assert _next_planned_start([]) is None


def test_day_statistics_over_todays_published_hours() -> None:
    today = [{"value": 0.10}, {"value": 0.30}, {"value": 0.20}]

    stats = _day_statistics(today, current=0.30)

    assert stats["average"] == pytest.approx(0.20)
    assert stats["min"] == 0.10
    assert stats["max"] == 0.30
    assert stats["price_percent_to_average"] == pytest.approx(150.0)


def test_day_statistics_skip_the_percentage_on_a_non_positive_average() -> None:
    """Negative prices are routine here, and a percentage of them says nothing."""
    today = [{"value": -0.02}, {"value": 0.01}]

    stats = _day_statistics(today, current=-0.02)

    assert stats["average"] == pytest.approx(-0.005)
    assert stats["price_percent_to_average"] is None


def test_day_statistics_without_data() -> None:
    stats = _day_statistics([], current=0.1)

    assert stats == {
        "average": None,
        "min": None,
        "max": None,
        "price_percent_to_average": None,
    }


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
    assert f"{entry.entry_id}_weekend_hours_next_start" not in unique_ids
    assert f"{entry.entry_id}_is_in_weekend_hours" not in unique_ids
    assert f"{entry.entry_id}_retail_cheapest_window_average_price" not in unique_ids
    assert f"{entry.entry_id}_retail_cheapest_window_start" not in unique_ids
    assert f"{entry.entry_id}_retail_cheapest_window_end" not in unique_ids
    assert f"{entry.entry_id}_retail_cheapest_window_active" not in unique_ids


async def test_plan_saving_sensors_report_what_the_plan_gained(hass, freezer) -> None:
    """The plan's average and its distance from the block average, end to end."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T00:00:00+00:00")
    # A four-hour block priced 0.10 / 0.20 / 0.30 / 0.40. Picking the two
    # cheapest gives 0.15 against a block average of 0.25 - 40% below it.
    entries = [
        {
            "start": f"2026-08-08T{hour:02d}:00:00Z",
            "end": f"2026-08-08T{hour + 1:02d}:00:00Z",
            "value": value,
            "source": "day_ahead",
        }
        for hour, value in enumerate([0.10, 0.20, 0.30, 0.40])
    ]

    entry = await _setup_entry(
        hass,
        extra_data={"cheapest_hours_count": 2, "cheapest_hours_window_hours": 4},
        price_entries=entries,
    )

    average = _state_for_unique_id(hass, entry, "cheapest_hours_average_price")
    assert float(average.state) == pytest.approx(0.15)
    assert average.attributes["window_average_value"] == pytest.approx(0.25)

    saving = _state_for_unique_id(hass, entry, "cheapest_hours_saving")
    assert float(saving.state) == pytest.approx(40.0)
    assert saving.attributes["unit_of_measurement"] == "%"


async def test_saving_sensors_are_absent_without_a_plan(hass) -> None:
    entry = await _setup_entry(hass)

    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }

    assert f"{entry.entry_id}_cheapest_hours_average_price" not in unique_ids
    assert f"{entry.entry_id}_cheapest_hours_saving" not in unique_ids
    # The window countdowns come from the summary and are always created.
    assert f"{entry.entry_id}_cheapest_window_remaining" in unique_ids
    assert f"{entry.entry_id}_greenest_window_remaining" in unique_ids


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


async def test_price_series_sensor_exposes_forecast_only_entries(hass, freezer) -> None:
    """raw_forecast contains only entries whose source is "forecast", any date."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T10:00:00+00:00")
    entries = [
        {
            "start": "2026-08-08T11:00:00Z",
            "end": "2026-08-08T12:00:00Z",
            "value": 0.11,
            "source": "day_ahead",
        },
        {
            "start": "2026-08-09T05:00:00Z",
            "end": "2026-08-09T06:00:00Z",
            "value": 0.13,
            "source": "day_ahead",
        },
        {
            "start": "2026-08-10T05:00:00Z",
            "end": "2026-08-10T06:00:00Z",
            "value": 0.15,
            "source": "forecast",
        },
    ]

    entry = await _setup_entry(hass, price_entries=entries)

    state = _state_for_unique_id(hass, entry, "price_series")

    assert [item["value"] for item in state.attributes["raw_forecast"]] == [0.15]
    # forecast entries beyond tomorrow must not leak into raw_today/raw_tomorrow
    assert [item["value"] for item in state.attributes["raw_tomorrow"]] == [0.13]


async def test_forecast_entries_stay_out_of_raw_today_and_tomorrow(
    hass, freezer
) -> None:
    """Tomorrow stays empty until its day-ahead prices are published.

    Before this was enforced, a day whose day-ahead was not out yet had its
    forecast entries land in raw_tomorrow as well as raw_forecast. A chart
    then drew them once as "known day-ahead" and once as forecast - and
    because the forecast series starts before raw_tomorrow ends, joining
    the two series sent the line backwards in time, drawing a flat stretch
    across the overlap.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T10:00:00+00:00")
    entries = [
        {
            "start": "2026-08-08T11:00:00Z",
            "end": "2026-08-08T12:00:00Z",
            "value": 0.11,
            "source": "day_ahead",
        },
        {
            "start": "2026-08-09T05:00:00Z",
            "end": "2026-08-09T06:00:00Z",
            "value": 0.13,
            "source": "forecast",
        },
    ]

    entry = await _setup_entry(hass, price_entries=entries)

    state = _state_for_unique_id(hass, entry, "price_series")

    assert [item["value"] for item in state.attributes["raw_today"]] == [0.11]
    assert state.attributes["raw_tomorrow"] == []
    assert [item["value"] for item in state.attributes["raw_forecast"]] == [0.13]


async def test_entries_without_a_source_still_count_as_known(hass, freezer) -> None:
    """Only an explicit "forecast" is excluded, not an unlabelled entry."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T10:00:00+00:00")
    entries = [
        {"start": "2026-08-08T11:00:00Z", "end": "2026-08-08T12:00:00Z", "value": 0.11},
        {"start": "2026-08-09T05:00:00Z", "end": "2026-08-09T06:00:00Z", "value": 0.13},
    ]

    entry = await _setup_entry(hass, price_entries=entries)

    state = _state_for_unique_id(hass, entry, "price_series")

    assert [item["value"] for item in state.attributes["raw_today"]] == [0.11]
    assert [item["value"] for item in state.attributes["raw_tomorrow"]] == [0.13]


async def test_retail_price_sensor_includes_raw_series(hass, freezer) -> None:
    """retail_current_price also exposes raw_today/raw_tomorrow, like price_series."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T10:00:00+00:00")
    entries = [
        {"start": "2026-08-08T11:00:00Z", "end": "2026-08-08T12:00:00Z", "value": 0.31},
        {"start": "2026-08-09T05:00:00Z", "end": "2026-08-09T06:00:00Z", "value": 0.33},
    ]

    entry = await _setup_entry(
        hass,
        extra_data={"retail_pricing": True, "postal_code": "10115"},
        price_entries=entries,
    )

    state = _state_for_unique_id(hass, entry, "retail_current_price")

    assert [item["value"] for item in state.attributes["raw_today"]] == [0.31]
    assert [item["value"] for item in state.attributes["raw_tomorrow"]] == [0.33]


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
    assert f"{entry.entry_id}_retail_cheapest_window_average_price" in unique_ids
    assert f"{entry.entry_id}_retail_cheapest_window_start" in unique_ids
    assert f"{entry.entry_id}_retail_cheapest_window_end" in unique_ids
    assert f"{entry.entry_id}_retail_cheapest_window_active" in unique_ids


async def test_retail_window_sensors_use_retail_summary(hass) -> None:
    """retail_cheapest_window_* reads the retail-mode summary, not the base one."""
    retail_summary = {
        **SUMMARY_PAYLOAD,
        "flat": {
            **SUMMARY_PAYLOAD["flat"],
            "best_price_window_avg_price": 0.42,
            "best_price_window_start": "2026-08-08T05:00:00Z",
            "best_price_window_end": "2026-08-08T09:00:00Z",
            "is_cheapest_window_now": True,
        },
    }

    entry = await _setup_entry(
        hass,
        extra_data={"retail_pricing": True, "postal_code": "10115"},
        retail_summary_payload=retail_summary,
    )

    retail_price_state = _state_for_unique_id(
        hass, entry, "retail_cheapest_window_average_price"
    )
    assert retail_price_state.state == "0.42"

    # the base (spot) window sensor must stay on the base summary, unaffected
    base_price_state = _state_for_unique_id(hass, entry, "cheapest_window_average_price")
    assert base_price_state.state == "0.15"

    binary_state = _state_for_unique_id(hass, entry, "retail_cheapest_window_active")
    assert binary_state.state == "on"


async def test_weekend_hours_entities_created_when_enabled(hass, freezer) -> None:
    """weekend_hours_next_start and is_in_weekend_hours appear once configured."""
    from datetime import datetime, timedelta

    await hass.config.async_set_time_zone("UTC")
    # A Saturday, so the current weekend block is 2026-08-15T00:00 - 08-17T00:00.
    freezer.move_to("2026-08-15T00:00:00+00:00")
    start = datetime(2026, 8, 15, 0, 0)
    entries = [
        {
            "start": (start + timedelta(hours=h)).isoformat() + "Z",
            "end": (start + timedelta(hours=h + 1)).isoformat() + "Z",
            "value": 0.05 if h == 2 else 0.50,
        }
        for h in range(48)
    ]

    entry = await _setup_entry(
        hass,
        extra_data={"weekend_hours_count": 1},
        price_entries=entries,
    )

    registry = er.async_get(hass)
    entries_reg = er.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = {e.unique_id for e in entries_reg}
    assert f"{entry.entry_id}_weekend_hours_next_start" in unique_ids
    assert f"{entry.entry_id}_is_in_weekend_hours" in unique_ids

    start_state = _state_for_unique_id(hass, entry, "weekend_hours_next_start")
    assert start_state.state == "2026-08-15T02:00:00+00:00"

    active_state = _state_for_unique_id(hass, entry, "is_in_weekend_hours")
    assert active_state.state == "off"
