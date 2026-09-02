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
    summary_extra: dict | None = None,
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

    summary = {**SUMMARY_PAYLOAD, **(summary_extra or {})}

    async def _summary_side_effect(
        price_mode="base", postal_code=None, include_series=False
    ):
        if price_mode == "retail":
            return retail_summary_payload or summary
        return summary

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


async def test_plan_is_priced_on_retail_when_retail_pricing_is_on(
    hass, freezer
) -> None:
    """With retail enabled the plan must cost and save in retail terms.

    Pricing the plan on spot while the user pays retail overstates the
    saving badly: the markup is a large, roughly fixed addition, so the same
    hours look far cheaper relative to a spot baseline than to the price
    actually billed. Against live data a real 43% came out as 100%.
    """
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T00:00:00+00:00")

    def series(values):
        return [
            {
                "start": f"2026-08-08T{hour:02d}:00:00Z",
                "end": f"2026-08-08T{hour + 1:02d}:00:00Z",
                "value": value,
                "source": "day_ahead",
            }
            for hour, value in enumerate(values)
        ]

    # Same shape, shifted by a 0.20 markup: identical picks, but the plan
    # average and the saving both have to follow the retail numbers.
    base = series([0.10, 0.20, 0.30, 0.40])
    retail = series([0.30, 0.40, 0.50, 0.60])

    async def _prices(price_mode="base", postal_code=None):
        return {
            "format": "home-assistant-prices",
            "country": "DE",
            "unit": "EUR/kWh",
            "entries": retail if price_mode == "retail" else base,
        }

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="DE",
        data={
            "market": "DE",
            "horizon_hours": 48,
            "window_hours": 4,
            "update_interval_minutes": 30,
            "retail_pricing": True,
            "postal_code": "10115",
            "cheapest_hours_count": 2,
            "cheapest_hours_window_hours": 4,
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
            ".async_get_summary",
            new=AsyncMock(return_value=SUMMARY_PAYLOAD),
        ),
        patch(
            "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
            ".async_get_prices",
            new=AsyncMock(side_effect=_prices),
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    average = _state_for_unique_id(hass, entry, "cheapest_hours_average_price")
    # (0.30 + 0.40) / 2 on retail, not (0.10 + 0.20) / 2 on spot.
    assert float(average.state) == pytest.approx(0.35)
    assert average.attributes["window_average_value"] == pytest.approx(0.45)

    saving = _state_for_unique_id(hass, entry, "cheapest_hours_saving")
    # 22.2% against the retail baseline - on spot the same hours would have
    # claimed 40%.
    assert float(saving.state) == pytest.approx(22.2, abs=0.1)


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


FORECAST_QUALITY = {
    "available": True,
    "error": None,
    "price_window": {
        "window_hours": 4,
        "price_basis": "base",
        "evaluated_days": 30,
        "exact_hit_days": 17,
        "within_one_hour_days": 28,
        "mean_extra_cost": 0.001459,
        "mean_extra_cost_unit": "EUR/kWh",
        "period_start": "2026-08-02",
        "period_end": "2026-08-31",
    },
}


async def test_forecast_quality_sensor_reports_the_extra_cost(hass) -> None:
    """State is the extra cost; the hit counts ride along as attributes."""
    entry = await _setup_entry(
        hass, summary_extra={"forecast_quality": FORECAST_QUALITY}
    )

    state = _state_for_unique_id(hass, entry, "forecast_quality")

    assert float(state.state) == pytest.approx(0.001459)
    assert state.attributes["unit_of_measurement"] == "EUR/kWh"
    assert state.attributes["exact_hit_days"] == 17
    assert state.attributes["within_one_hour_days"] == 28
    assert state.attributes["evaluated_days"] == 30
    assert state.attributes["window_hours"] == 4
    assert state.attributes["price_basis"] == "base"
    # Precomputed so a card never has to divide - and never divides by zero.
    assert state.attributes["exact_hit_percent"] == pytest.approx(56.7)
    assert state.attributes["within_one_hour_percent"] == pytest.approx(93.3)


async def test_forecast_quality_sensor_is_unavailable_without_a_figure(hass) -> None:
    """A market with too little history must show nothing, not a zero."""
    entry = await _setup_entry(
        hass,
        summary_extra={
            "forecast_quality": {
                "available": False,
                "error": "insufficient_history",
                "price_window": None,
            }
        },
    )

    state = _state_for_unique_id(hass, entry, "forecast_quality")

    assert state.state == "unavailable"


async def test_forecast_quality_sensor_survives_a_missing_block(hass) -> None:
    """An API that does not send the block at all must not break the entity."""
    entry = await _setup_entry(hass)

    state = _state_for_unique_id(hass, entry, "forecast_quality")

    assert state.state == "unavailable"


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


def _co2_slots(start_iso: str, values_by_hour: dict[int, float]) -> list[dict]:
    """Hourly CO2 slots in the shape the summary's series.co2 has."""
    from datetime import datetime, timedelta

    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    return [
        {
            "start": (start + timedelta(hours=hour)).isoformat().replace("+00:00", "Z"),
            "end": (start + timedelta(hours=hour + 1)).isoformat().replace("+00:00", "Z"),
            "value": value,
            "unit": "gCO2/kWh",
            "source": None,
        }
        for hour, value in sorted(values_by_hour.items())
    ]


async def test_combined_score_is_rescaled_so_higher_is_better(hass) -> None:
    """The API's 0-is-best ranking key must reach the user as 100-is-best."""
    entry = await _setup_entry(hass)

    # SUMMARY_PAYLOAD carries a raw score of 0.2, a good window.
    assert _state_for_unique_id(hass, entry, "combined_window_score").state == "80.0"


async def test_combined_score_keeps_the_raw_key_as_an_attribute(hass) -> None:
    """Nobody who read the old value loses access to it."""
    entry = await _setup_entry(
        hass,
        summary_extra={
            "combined": {
                "best_window_next_horizon": {
                    "start": "2026-08-08T04:00:00Z",
                    "end": "2026-08-08T08:00:00Z",
                    "duration_hours": 4,
                    "score": 0.2,
                    "average_price_value": 0.15,
                    "average_co2_g_kwh": 250.0,
                    "method": "equal_weight_normalized_price_and_co2",
                }
            }
        },
    )

    state = _state_for_unique_id(hass, entry, "combined_window_score")
    assert state.attributes["raw_score"] == 0.2
    assert state.attributes["average_price_value"] == 0.15
    assert state.attributes["average_co2_g_kwh"] == 250.0
    assert state.attributes["window_hours"] == 4


async def test_combined_window_start_end_and_active(hass, freezer) -> None:
    """The window itself is what an automation acts on, so it has entities."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T05:00:00+00:00")

    entry = await _setup_entry(
        hass,
        summary_extra={
            "flat": {
                **SUMMARY_PAYLOAD["flat"],
                "combined_window_start": "2026-08-08T04:00:00Z",
                "combined_window_end": "2026-08-08T08:00:00Z",
            }
        },
    )

    assert (
        _state_for_unique_id(hass, entry, "combined_window_start").state
        == "2026-08-08T04:00:00+00:00"
    )
    assert (
        _state_for_unique_id(hass, entry, "combined_window_end").state
        == "2026-08-08T08:00:00+00:00"
    )
    assert _state_for_unique_id(hass, entry, "combined_window_active").state == "on"


async def test_co2_series_sensor_publishes_the_series(hass, freezer) -> None:
    """CO2 was chartable nowhere before this - now it carries the same shape."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T04:30:00+00:00")

    entry = await _setup_entry(
        hass,
        summary_extra={
            "co2": {"available": True, "unit": "gCO2/kWh"},
            "series": {"co2": _co2_slots("2026-08-08T04:00:00Z", {0: 300.0, 1: 120.0})},
        },
    )

    state = _state_for_unique_id(hass, entry, "co2_series")
    assert state.state == "300.0"
    assert state.attributes["unit_of_measurement"] == "gCO2/kWh"
    assert len(state.attributes["raw_today"]) == 2
    assert state.attributes["min"] == 120.0
    assert state.attributes["max"] == 300.0


async def test_co2_series_sensor_is_unavailable_without_co2_data(hass) -> None:
    """A market with no CO2 coverage must not show a stale or empty number."""
    entry = await _setup_entry(hass)

    assert _state_for_unique_id(hass, entry, "co2_series").state == "unavailable"


async def test_greenest_hours_entities_created_when_enabled(hass, freezer) -> None:
    """The CO2 plan mirrors the price plan, picked from the same block."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T00:00:00+00:00")

    # Hour 3 is by far the cleanest, hour 5 the next cleanest.
    values = {hour: 400.0 for hour in range(24)}
    values.update({3: 90.0, 5: 150.0})

    entry = await _setup_entry(
        hass,
        extra_data={"greenest_hours_count": 2, "cheapest_hours_window_hours": 24},
        summary_extra={
            "co2": {"available": True, "unit": "gCO2/kWh"},
            "series": {"co2": _co2_slots("2026-08-08T00:00:00Z", values)},
        },
    )

    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert f"{entry.entry_id}_greenest_hours_next_start" in unique_ids
    assert f"{entry.entry_id}_greenest_hours_average_co2" in unique_ids
    assert f"{entry.entry_id}_greenest_hours_saving" in unique_ids
    assert f"{entry.entry_id}_is_in_greenest_hours" in unique_ids

    assert (
        _state_for_unique_id(hass, entry, "greenest_hours_next_start").state
        == "2026-08-08T03:00:00+00:00"
    )
    # (90 + 150) / 2, in gCO2/kWh - not in the price series' currency.
    average = _state_for_unique_id(hass, entry, "greenest_hours_average_co2")
    assert average.state == "120.0"
    assert average.attributes["unit_of_measurement"] == "gCO2/kWh"


async def test_greenest_hours_saving_compares_against_the_block(hass, freezer) -> None:
    """The saving is against the block's own average, as on the price side."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-08-08T00:00:00+00:00")

    # Two hours at 100, two at 300: block average 200, plan average 100.
    values = {0: 100.0, 1: 100.0, 2: 300.0, 3: 300.0}

    entry = await _setup_entry(
        hass,
        extra_data={"greenest_hours_count": 2, "cheapest_hours_window_hours": 4},
        summary_extra={
            "co2": {"available": True, "unit": "gCO2/kWh"},
            "series": {"co2": _co2_slots("2026-08-08T00:00:00Z", values)},
        },
    )

    assert _state_for_unique_id(hass, entry, "greenest_hours_saving").state == "50.0"


async def test_no_greenest_entities_without_a_count(hass) -> None:
    """The CO2 plan stays opt-in, like every other plan."""
    entry = await _setup_entry(hass)

    registry = er.async_get(hass)
    unique_ids = {
        e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert f"{entry.entry_id}_greenest_hours_next_start" not in unique_ids
    assert f"{entry.entry_id}_is_in_greenest_hours" not in unique_ids
