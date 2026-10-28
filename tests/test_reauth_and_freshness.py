"""A refused key asks for a new one; an old series says that it is old."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import data_entry_flow
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energypriceforecast.api import (
    EnergyPriceForecastApiError,
    EnergyPriceForecastAuthError,
)
from custom_components.energypriceforecast.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_coordinator import SUMMARY_PAYLOAD, _full_day_entries, _setup_entry

DOMAIN = "energypriceforecast"
API = "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
KEYED_DATA = {
    "market": "DE",
    "horizon_hours": 120,
    "window_hours": 4,
    "api_key": "expired-key",
    "update_interval_minutes": 30,
    "cheapest_hours_count": 0,
    "retail_source": "off",
}


def _keyed_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="DE", version=3, data=KEYED_DATA)
    entry.add_to_hass(hass)
    return entry


async def _start_reauth(hass, entry):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )


async def test_a_refused_key_stops_the_entry_and_starts_reauth(hass) -> None:
    entry = _keyed_entry(hass)
    with patch(
        f"{API}.async_get_summary",
        new=AsyncMock(side_effect=EnergyPriceForecastAuthError("state: expired")),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert flows[0]["step_id"] == "reauth_confirm"


async def test_a_network_failure_is_not_mistaken_for_a_bad_key(hass) -> None:
    entry = _keyed_entry(hass)
    with patch(
        f"{API}.async_get_summary",
        new=AsyncMock(side_effect=EnergyPriceForecastApiError("timeout")),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.flow.async_progress() == []
    # Cancels the scheduled retry, which would otherwise outlive the test.
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reauth_stores_the_new_key(hass) -> None:
    entry = _keyed_entry(hass)
    result = await _start_reauth(hass, entry)
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"market": "DE"}

    validate = AsyncMock(return_value=None)
    with (
        patch("custom_components.energypriceforecast.config_flow._validate_input", new=validate),
        patch("custom_components.energypriceforecast.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "  fresh-key  "}
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "fresh-key"
    # Everything else of the entry is left exactly as it was.
    assert {k: v for k, v in entry.data.items() if k != "api_key"} == {
        k: v for k, v in KEYED_DATA.items() if k != "api_key"
    }
    assert validate.await_args.args[1]["api_key"] == "fresh-key"


async def test_reauth_without_a_key_falls_back_to_public_access(hass) -> None:
    entry = _keyed_entry(hass)
    result = await _start_reauth(hass, entry)
    with (
        patch(
            "custom_components.energypriceforecast.config_flow._validate_input",
            new=AsyncMock(return_value=None),
        ),
        patch("custom_components.energypriceforecast.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": ""}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert "api_key" not in entry.data
    assert entry.data["horizon_hours"] == 120


async def test_reauth_keeps_asking_while_the_new_key_is_refused(hass) -> None:
    entry = _keyed_entry(hass)
    result = await _start_reauth(hass, entry)
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(side_effect=EnergyPriceForecastAuthError("invalid")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"api_key": "also-wrong"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["api_key"] == "expired-key"


def _series_state(hass, entry):
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_price_series"
    )
    return hass.states.get(entity_id)


async def test_a_kept_series_is_marked_stale_until_it_is_fresh_again(hass, freezer) -> None:
    freezer.move_to("2026-10-28T10:00:00+00:00")
    entries = _full_day_entries("2026-10-28T00:00:00Z", {h: 0.2 for h in range(24)})
    entry = await _setup_entry(hass, price_entries=entries)

    state = _series_state(hass, entry)
    assert state.attributes["data_status"] == "fresh"
    assert state.attributes["data_updated"] == "2026-10-28T10:00:00+00:00"

    freezer.move_to("2026-10-28T10:30:00+00:00")
    with (
        patch(f"{API}.async_get_summary", new=AsyncMock(return_value=SUMMARY_PAYLOAD)),
        patch(
            f"{API}.async_get_prices",
            new=AsyncMock(side_effect=EnergyPriceForecastApiError("timeout")),
        ),
    ):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    state = _series_state(hass, entry)
    # Still there and still priced - only now it says how old it is.
    assert state.state == "0.2"
    assert state.attributes["data_status"] == "stale"
    assert state.attributes["data_updated"] == "2026-10-28T10:00:00+00:00"

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["sources"]["summary"]["status"] == "fresh"
    assert diagnostics["sources"]["price_series"]["status"] == "stale"
    assert diagnostics["sources"]["price_series"]["age_minutes"] == 30
    assert diagnostics["sources"]["price_series"]["last_error"] == "timeout"
    assert diagnostics["series_continuity"]["price_series"]["slots"] == 96
    assert diagnostics["series_continuity"]["price_series"]["gaps"] == 0
    assert diagnostics["config"]["market"] == "DE"


async def _local_day_today(hass, freezer, now: str, first_slot: str, hours: int):
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to(now)
    entries = _full_day_entries(first_slot, {h: 0.2 for h in range(hours)})
    entry = await _setup_entry(hass, price_entries=entries)
    return _series_state(hass, entry), entry


async def test_the_25_hour_autumn_day_is_shown_whole(hass, freezer) -> None:
    state, entry = await _local_day_today(
        hass, freezer, "2026-10-25T12:00:00+00:00", "2026-10-24T22:00:00Z", 25
    )
    today = state.attributes["raw_today"]
    assert len(today) == 100
    assert today[0]["start"].startswith("2026-10-24T22:00")
    assert today[-1]["end"].startswith("2026-10-25T23:00")
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["series_continuity"]["price_series"]["gaps"] == 0
    assert diagnostics["series_continuity"]["price_series"]["overlaps"] == 0


async def test_the_23_hour_spring_day_is_shown_whole(hass, freezer) -> None:
    state, _ = await _local_day_today(
        hass, freezer, "2027-03-28T12:00:00+00:00", "2027-03-27T23:00:00Z", 23
    )
    today = state.attributes["raw_today"]
    assert len(today) == 92
    assert today[-1]["end"].startswith("2027-03-28T22:00")
