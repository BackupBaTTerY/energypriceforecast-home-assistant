"""The "price spike possible" hint: a warning beside the prices, never a price."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_coordinator import SUMMARY_PAYLOAD, _full_day_entries

DOMAIN = "energypriceforecast"
API = "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
HINT_OK = {
    "status": "ok",
    "min_score": 0.99,
    "local_hours": "16:00-23:00",
    "marked_slots": 8,
    "updated_at": "2026-11-17T08:58:00Z",
}


def _entries() -> list[dict]:
    """09:00-23:00 UTC as forecast; 17:00 and 18:00 UTC (18-20 local) marked."""
    entries = _full_day_entries(
        "2026-11-17T09:00:00Z", {h: 0.25 for h in range(14)}, source="forecast"
    )
    for entry in entries:
        if entry["start"].startswith(("2026-11-17T17:", "2026-11-17T18:")):
            entry["spike_possible"] = True
    return entries


async def _setup(hass, market: str = "DE", hint: dict | None = HINT_OK, entries=None,
                 enable: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=market,
        data={
            "market": market,
            "horizon_hours": 48,
            "window_hours": 4,
            "update_interval_minutes": 30,
            "cheapest_hours_count": 0,
            "retail_source": "off",
        },
    )
    entry.add_to_hass(hass)
    if enable:
        # The entity is disabled by default; a user enables it on the device page.
        er.async_get(hass).async_get_or_create(
            "binary_sensor",
            DOMAIN,
            f"{entry.entry_id}_price_spike_possible",
            suggested_object_id="epf_price_spike_possible",
            disabled_by=None,
        )
    payload = {
        "format": "home-assistant-prices",
        "country": market,
        "currency": "EUR",
        "unit": "EUR/kWh",
        "entries": _entries() if entries is None else entries,
        "source": {"spike_hint": hint} if hint is not None else {},
    }
    summary = {**SUMMARY_PAYLOAD, "country": market}
    with (
        patch(f"{API}.async_get_summary", new=AsyncMock(return_value=summary)),
        patch(f"{API}.async_get_prices", new=AsyncMock(return_value=payload)),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _entity_id(hass, entry) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_price_spike_possible"
    )


async def test_a_marked_evening_turns_the_sensor_on(hass, freezer) -> None:
    freezer.move_to("2026-11-17T10:00:00+00:00")
    entry = await _setup(hass)
    state = hass.states.get(_entity_id(hass, entry))
    assert state.state == "on"
    assert state.attributes["next_start"] == "2026-11-17T17:00:00+00:00"
    assert state.attributes["next_end"] == "2026-11-17T19:00:00+00:00"
    assert state.attributes["periods"] == [
        {"start": "2026-11-17T17:00:00+00:00", "end": "2026-11-17T19:00:00+00:00"}
    ]
    assert state.attributes["min_score"] == 0.99
    assert state.attributes["hint_updated"] == "2026-11-17T08:58:00Z"


async def test_the_series_keeps_its_prices_and_names_the_marked_slots(hass, freezer) -> None:
    freezer.move_to("2026-11-17T10:00:00+00:00")
    entry = await _setup(hass)
    series = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_price_series"
    )
    forecast = hass.states.get(series).attributes["raw_forecast"]
    marked = [slot for slot in forecast if slot.get("spike_possible")]
    assert len(marked) == 8
    assert {slot["value"] for slot in forecast} == {0.25}
    assert all("spike_possible" not in slot for slot in forecast if slot not in marked)


async def test_without_a_judgement_the_sensor_is_unavailable(hass, freezer) -> None:
    freezer.move_to("2026-11-17T10:00:00+00:00")
    for hint in ({"status": "stale", "min_score": 0.99}, None):
        hass_entry = await _setup(hass, hint=hint)
        assert hass.states.get(_entity_id(hass, hass_entry)).state == "unavailable"
        await hass.config_entries.async_unload(hass_entry.entry_id)
        await hass.config_entries.async_remove(hass_entry.entry_id)
        await hass.async_block_till_done()


async def test_once_the_evening_has_passed_it_turns_off(hass, freezer) -> None:
    freezer.move_to("2026-11-17T10:00:00+00:00")
    entry = await _setup(hass, entries=[
        {**slot, "spike_possible": True} for slot in _full_day_entries(
            "2026-11-17T09:00:00Z", {0: 0.25}, source="forecast")
    ])
    state = hass.states.get(_entity_id(hass, entry))
    assert state.state == "off"
    assert state.attributes["next_start"] is None
    assert state.attributes["periods"] == []


async def test_it_is_disabled_by_default(hass, freezer) -> None:
    freezer.move_to("2026-11-17T10:00:00+00:00")
    entry = await _setup(hass, enable=False)
    entity_id = _entity_id(hass, entry)
    assert entity_id is not None
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(entity_id) is None


async def test_markets_without_the_index_do_not_get_it(hass, freezer) -> None:
    freezer.move_to("2026-11-17T10:00:00+00:00")
    entry = await _setup(hass, market="ES", enable=False)
    assert _entity_id(hass, entry) is None
