"""The ready-made automations load in Home Assistant and do what they say."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components import automation
from homeassistant.components.blueprint import models
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

BLUEPRINTS = Path(__file__).parent.parent / "blueprints" / "automation"
PLAN = "energypriceforecast/run_device_on_price_plan.yaml"
APPLIANCE = "energypriceforecast/start_appliance_before_deadline.yaml"
TOMORROW = "energypriceforecast/tomorrow_price_notification.yaml"
SERIES = "sensor.energy_price_forecast_eu_de_price_forecast_series"


def _blueprint(path: str) -> models.Blueprint:
    data = yaml_util.load_yaml(str(BLUEPRINTS / path))
    try:
        return models.Blueprint(
            data,
            expected_domain="automation",
            path=path,
            schema=automation.config.AUTOMATION_BLUEPRINT_SCHEMA,
        )
    except TypeError:  # Older Home Assistant without the schema argument.
        return models.Blueprint(data, expected_domain="automation", path=path)


@contextmanager
def _our_blueprints():
    with patch(
        "homeassistant.components.blueprint.models.DomainBlueprints._load_blueprint",
        side_effect=_blueprint,
    ):
        yield


async def _automation(hass, path: str, inputs: dict) -> None:
    with _our_blueprints():
        assert await async_setup_component(
            hass,
            "automation",
            {"automation": {"use_blueprint": {"path": path, "input": inputs}}},
        )
        await hass.async_block_till_done()
    states = hass.states.async_all("automation")
    assert len(states) == 1
    assert states[0].state == "on", states[0].attributes


async def _until(condition, rounds: int = 200) -> None:
    """Let the event loop run until condition() holds.

    Not async_block_till_done: that would wait for an automation that is
    deliberately waiting for its start time, and never return.
    """
    for _ in range(rounds):
        if condition():
            return
        # sleep(0) only yields: the frozen test clock never lets a timed
        # sleep end.
        await asyncio.sleep(0)


def test_every_blueprint_names_its_own_source() -> None:
    files = sorted(p.relative_to(BLUEPRINTS).as_posix() for p in BLUEPRINTS.rglob("*.yaml"))
    assert files == sorted([APPLIANCE, PLAN, TOMORROW])
    for path in files:
        meta = _blueprint(path).metadata
        assert meta["source_url"].endswith("/blob/main/blueprints/automation/" + path)
        assert meta["homeassistant"]["min_version"] == "2025.6.0"


def _slots(first: datetime, values: list[float], minutes: int = 60) -> list[dict]:
    step = timedelta(minutes=minutes)
    return [
        {
            "start": (first + i * step).isoformat(),
            "end": (first + (i + 1) * step).isoformat(),
            "value": value,
        }
        for i, value in enumerate(values)
    ]


async def test_the_plan_switches_devices_on_and_off(hass) -> None:
    on = async_mock_service(hass, "homeassistant", "turn_on")
    off = async_mock_service(hass, "homeassistant", "turn_off")
    hass.states.async_set("binary_sensor.epf_cheapest_hours_active", "off")
    await _automation(
        hass,
        PLAN,
        {
            "plan_sensor": "binary_sensor.epf_cheapest_hours_active",
            "devices": ["switch.heat_pump", "switch.boiler"],
            "when_unavailable": "off",
        },
    )

    hass.states.async_set("binary_sensor.epf_cheapest_hours_active", "on")
    await hass.async_block_till_done()
    assert len(on) == 1
    assert on[0].data["entity_id"] == ["switch.heat_pump", "switch.boiler"]

    hass.states.async_set("binary_sensor.epf_cheapest_hours_active", "off")
    await hass.async_block_till_done()
    assert len(off) == 1

    # No plan yet: the chosen fallback, here off.
    hass.states.async_set("binary_sensor.epf_cheapest_hours_active", "unavailable")
    await hass.async_block_till_done()
    assert len(off) == 2
    assert len(on) == 1


async def test_without_a_plan_the_devices_can_be_left_alone(hass) -> None:
    on = async_mock_service(hass, "homeassistant", "turn_on")
    off = async_mock_service(hass, "homeassistant", "turn_off")
    hass.states.async_set("binary_sensor.epf_cheapest_window_active", "on")
    await _automation(
        hass,
        PLAN,
        {
            "plan_sensor": "binary_sensor.epf_cheapest_window_active",
            "devices": ["switch.charger"],
        },
    )
    hass.states.async_set("binary_sensor.epf_cheapest_window_active", "unavailable")
    await hass.async_block_till_done()
    assert on == []
    assert off == []


async def test_the_appliance_starts_in_the_cheapest_stretch_before_the_deadline(
    hass, freezer
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    # 20:00 local on a Friday; finish by 07:00 tomorrow.
    freezer.move_to("2026-11-06T19:00:00+00:00")
    first = datetime(2026, 11, 6, 19, 0, tzinfo=timezone.utc)
    # 20:00 ... 07:00 local, then a cheaper day later that is past the deadline.
    values = [0.30, 0.28, 0.25, 0.22, 0.12, 0.10, 0.13, 0.20, 0.24, 0.26, 0.29, 0.31]
    later = [0.01] * 6
    hass.states.async_set(
        SERIES,
        "0.30",
        {
            "unit_of_measurement": "EUR/kWh",
            "raw_today": _slots(first, values[:4]),
            "raw_tomorrow": [],
            "raw_forecast": _slots(first + timedelta(hours=4), values[4:] + later),
        },
    )
    hass.states.async_set("input_boolean.dishwasher_ready", "off")
    started = async_mock_service(hass, "test", "start")
    planned = async_mock_service(hass, "test", "planned")
    async_mock_service(hass, "homeassistant", "turn_off")
    await _automation(
        hass,
        APPLIANCE,
        {
            "price_sensor": SERIES,
            "ready_entity": "input_boolean.dishwasher_ready",
            "run_hours": 2,
            "finish_by": "07:00:00",
            "start_actions": [{"action": "test.start"}],
            "planned_actions": [
                {
                    "action": "test.planned",
                    "data": {"at": "{{ start_time }}", "price": "{{ average_price }}"},
                }
            ],
        },
    )

    hass.states.async_set("input_boolean.dishwasher_ready", "on")
    await _until(lambda: planned)
    # 00:00-02:00 local averages 0.11, the cheapest two hours before 07:00.
    assert len(planned) == 1
    assert planned[0].data["at"] == "00:00"
    assert float(planned[0].data["price"]) == pytest.approx(0.11)
    assert started == []

    freezer.move_to("2026-11-06T22:59:00+00:00")
    async_fire_time_changed(hass)
    await _until(lambda: started, rounds=20)
    assert started == []

    freezer.move_to("2026-11-06T23:00:30+00:00")
    async_fire_time_changed(hass)
    await _until(lambda: started)
    assert len(started) == 1
    await hass.async_block_till_done()


async def test_the_appliance_starts_at_once_without_enough_data(hass, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-06T19:00:00+00:00")
    hass.states.async_set(SERIES, "0.30", {"raw_today": [], "raw_forecast": []})
    hass.states.async_set("input_boolean.dishwasher_ready", "off")
    started = async_mock_service(hass, "test", "start")
    async_mock_service(hass, "homeassistant", "turn_off")
    await _automation(
        hass,
        APPLIANCE,
        {
            "price_sensor": SERIES,
            "ready_entity": "input_boolean.dishwasher_ready",
            "start_actions": [{"action": "test.start"}],
        },
    )
    hass.states.async_set("input_boolean.dishwasher_ready", "on")
    await _until(lambda: started)
    assert len(started) == 1
    await hass.async_block_till_done()


async def test_tomorrow_is_summed_up_from_published_prices(hass, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-06T16:59:30+00:00")
    tomorrow = datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)
    values = [0.20] * 24
    values[3] = 0.05  # 03:00 local
    values[18] = 0.45  # 18:00 local
    hass.states.async_set(
        SERIES,
        "0.25",
        {
            "unit_of_measurement": "EUR/kWh",
            "raw_tomorrow": _slots(tomorrow, values),
            "raw_forecast": [],
        },
    )
    notes = async_mock_service(hass, "persistent_notification", "create")
    await _automation(hass, TOMORROW, {"price_sensor": SERIES})

    freezer.move_to("2026-11-06T17:00:01+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert len(notes) == 1
    message = notes[0].data["message"]
    assert "Tomorrow averages 20.4 ct" in message
    assert "Cheapest at 03:00 (5.0 ct)" in message
    assert "dearest at 18:00 (45.0 ct)" in message
    assert "Based on published prices." in message


async def test_tomorrow_stays_quiet_while_it_is_incomplete(hass, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to("2026-11-06T16:59:30+00:00")
    tomorrow = datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)
    hass.states.async_set(
        SERIES,
        "0.25",
        {
            "unit_of_measurement": "EUR/kWh",
            # Only half of tomorrow is known so far.
            "raw_forecast": _slots(tomorrow, [0.40] * 12),
        },
    )
    notes = async_mock_service(hass, "persistent_notification", "create")
    await _automation(hass, TOMORROW, {"price_sensor": SERIES, "only_above": 0.3})

    freezer.move_to("2026-11-06T17:00:01+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert notes == []
