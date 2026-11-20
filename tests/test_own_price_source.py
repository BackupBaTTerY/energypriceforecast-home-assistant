"""The own price source (1.21.0) inside Home Assistant.

What own_prices.py decides is tested on its own in test_own_prices.py. Here
the same rules run through a real config entry: reading a Nord Pool or
ENTSO-e sensor, the core Nord Pool service, mixing on a state change without
asking the API again, the dialog and its check, and the diagnostics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from homeassistant.core import SupportsResponse
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energypriceforecast.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.energypriceforecast.own_prices import OWN_SOURCE

DOMAIN = "energypriceforecast"
API = "custom_components.energypriceforecast.api.EnergyPriceForecastApi"
NOW = "2026-11-20T10:00:00+00:00"  # 12:00 in Helsinki
START = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
HELSINKI_MIDNIGHT = datetime(2026, 11, 19, 22, 0, tzinfo=timezone.utc)

SUMMARY = {
    "api_version": "v1",
    "format": "home-assistant-summary",
    "generated_at": NOW,
    "flat": {"current_price": 0.30, "current_price_unit": "EUR/kWh"},
    "meta": {"allowed_horizon_hours": 48, "used_horizon_hours": 48},
}


def _api_entries(hours: int, value: float, source: str) -> list[dict]:
    return [
        {
            "start": (START + timedelta(hours=i)).isoformat(),
            "end": (START + timedelta(hours=i + 1)).isoformat(),
            "value": value,
            "source": source,
        }
        for i in range(hours)
    ]


def _prices(entries: list[dict], unit: str = "EUR/kWh") -> dict:
    return {"format": "home-assistant-prices", "unit": unit, "entries": entries}


def _raw(first: datetime, values: list[float]) -> list[dict]:
    """Nord Pool's raw_today shape, with datetimes as it keeps them."""
    return [
        {
            "start": first + timedelta(hours=i),
            "end": first + timedelta(hours=i + 1),
            "value": value,
        }
        for i, value in enumerate(values)
    ]


def _registered(hass, platform: str, unique_id: str, area: str | None = None) -> str:
    """An entity of another integration, as the selector would offer it."""
    other = MockConfigEntry(domain=platform, title=platform)
    other.add_to_hass(hass)
    device_id = None
    if area is not None:
        device_id = (
            dr.async_get(hass)
            .async_get_or_create(
                config_entry_id=other.entry_id, identifiers={(platform, area)}
            )
            .id
        )
    return (
        er.async_get(hass)
        .async_get_or_create(
            "sensor",
            platform,
            unique_id,
            config_entry=other,
            device_id=device_id,
            suggested_object_id=f"{platform}_{unique_id}".replace("-", "_"),
        )
        .entity_id
    )


async def _setup(hass, market: str, entries: list[dict], options: dict | None = None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=market,
        version=3,
        data={
            "market": market,
            "horizon_hours": 48,
            "window_hours": 4,
            "update_interval_minutes": 30,
            "retail_source": "off",
            "price_resolution": "hourly",
        },
        options=options or {},
    )
    entry.add_to_hass(hass)
    prices = AsyncMock(return_value=_prices(entries))
    with (
        patch(f"{API}.async_get_summary", new=AsyncMock(return_value=SUMMARY)),
        patch(f"{API}.async_get_prices", new=prices),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, prices


def _values(entry) -> list[tuple[str, float, str]]:
    return [
        (item["start"], item["value"], item["source"])
        for item in entry.runtime_data.price_series["entries"]
    ]


async def test_a_nordpool_sensor_replaces_the_forecast_it_covers(hass, freezer) -> None:
    """Finland: the API has only its forecast; Nord Pool has today in c/kWh."""
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to(NOW)
    entity = _registered(hass, "nordpool", "nordpool_kwh_fi_eur_3_095_0")
    hass.states.async_set(
        entity,
        "4.0",
        {
            "unit_of_measurement": "c/kWh",
            "raw_today": _raw(HELSINKI_MIDNIGHT, [float(i) for i in range(24)]),
            "raw_tomorrow": [],
        },
    )
    entry, _ = await _setup(
        hass, "FI", _api_entries(36, 0.30, "forecast"), {"own_price_entity": entity}
    )

    entries = entry.runtime_data.price_series["entries"]
    by_start = {datetime.fromisoformat(item["start"]): item for item in entries}
    # 12:00 Helsinki is hour 12 of the source: 12 c/kWh = 0.12 EUR/kWh.
    assert by_start[START]["value"] == 0.12
    assert by_start[START]["source"] == OWN_SOURCE
    # The morning already past is filled from the source as well.
    assert by_start[HELSINKI_MIDNIGHT]["value"] == 0.0
    # Tomorrow is not in the source yet: still the forecast.
    tomorrow = HELSINKI_MIDNIGHT + timedelta(days=1)
    assert by_start[tomorrow]["source"] == "forecast"

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    source = diagnostics["own_price_source"]
    assert source["status"] == "ok"
    assert source["format"] == "nordpool"
    assert source["merge"]["replaced_forecast_slots"] == 12
    # Counts, not the source's prices.
    assert "slots" in source and "raw_today" not in str(source)


async def test_tomorrow_arrives_without_asking_the_api_again(hass, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to(NOW)
    entity = _registered(hass, "nordpool", "nordpool_kwh_fi_eur_3_095_0")
    attributes = {
        "unit_of_measurement": "EUR/kWh",
        "raw_today": _raw(HELSINKI_MIDNIGHT, [0.10] * 24),
        "raw_tomorrow": [],
    }
    hass.states.async_set(entity, "0.10", attributes)
    entry, prices = await _setup(
        hass, "FI", _api_entries(36, 0.30, "forecast"), {"own_price_entity": entity}
    )
    calls = prices.await_count
    tomorrow = HELSINKI_MIDNIGHT + timedelta(days=1)

    hass.states.async_set(
        entity, "0.10", {**attributes, "raw_tomorrow": _raw(tomorrow, [0.02] * 24)}
    )
    await hass.async_block_till_done()

    by_start = {
        datetime.fromisoformat(item["start"]): item
        for item in entry.runtime_data.price_series["entries"]
    }
    assert by_start[tomorrow]["value"] == 0.02
    assert by_start[tomorrow]["source"] == OWN_SOURCE
    assert prices.await_count == calls


async def test_an_entsoe_sensor_is_read(hass, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to(NOW)
    entity = _registered(hass, "entsoe", "entsoe_avg_price")
    prices = [
        {"time": str(HELSINKI_MIDNIGHT + timedelta(hours=i)), "price": 0.05}
        for i in range(24)
    ]
    hass.states.async_set(
        entity,
        "0.05",
        {"unit_of_measurement": "€/kWh", "prices_today": prices, "prices": prices},
    )
    entry, _ = await _setup(
        hass, "FI", _api_entries(36, 0.30, "forecast"), {"own_price_entity": entity}
    )
    assert entry.runtime_data.own_source.format == "entsoe"
    assert (START.isoformat(), 0.05, OWN_SOURCE) in _values(entry)


async def test_another_currency_is_never_mixed(hass, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to(NOW)
    entity = _registered(hass, "nordpool", "nordpool_kwh_fi_sek_3_095_0")
    hass.states.async_set(
        entity,
        "1.0",
        {"unit_of_measurement": "SEK/kWh", "raw_today": _raw(HELSINKI_MIDNIGHT, [1.0] * 24)},
    )
    entry, _ = await _setup(
        hass, "FI", _api_entries(36, 0.30, "forecast"), {"own_price_entity": entity}
    )
    assert entry.runtime_data.own_source.status == "currency_mismatch"
    assert all(source == "forecast" for _, _, source in _values(entry))


async def test_a_missing_sensor_leaves_the_forecast_alone(hass, freezer) -> None:
    freezer.move_to(NOW)
    entry, _ = await _setup(
        hass,
        "FI",
        _api_entries(6, 0.30, "forecast"),
        {"own_price_entity": "sensor.gone"},
    )
    assert entry.runtime_data.own_source.status == "entity_missing"
    assert all(source == "forecast" for _, _, source in _values(entry))


async def test_core_nordpool_is_asked_through_its_service(hass, freezer) -> None:
    """The built-in Nord Pool has no price attributes, only a service."""
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to(NOW)
    entity = _registered(hass, "nordpool", "FI-current_price", area="FI")
    hass.states.async_set(entity, "0.05", {"unit_of_measurement": "EUR/kWh"})
    asked: list[dict] = []

    async def _get_prices(call):
        asked.append(dict(call.data))
        if call.data["date"] != "2026-11-20":
            return {"FI": []}
        return {
            "FI": [
                {
                    "start": (HELSINKI_MIDNIGHT + timedelta(hours=i)).isoformat(),
                    "end": (HELSINKI_MIDNIGHT + timedelta(hours=i + 1)).isoformat(),
                    "price": 40.0,  # EUR/MWh
                }
                for i in range(24)
            ]
        }

    hass.services.async_register(
        "nordpool",
        "get_prices_for_date",
        _get_prices,
        supports_response=SupportsResponse.ONLY,
    )
    entry, _ = await _setup(
        hass, "FI", _api_entries(36, 0.30, "forecast"), {"own_price_entity": entity}
    )
    assert entry.runtime_data.own_source.format == "nordpool_core"
    assert (START.isoformat(), 0.04, OWN_SOURCE) in _values(entry)
    assert asked[0]["areas"] == ["FI"]
    assert asked[0]["currency"] == "EUR"
    assert {call["date"] for call in asked} == {"2026-11-20", "2026-11-21"}

    # Today has arrived and is not asked for again; tomorrow is, but only
    # after a pause, so the hours before the auction are no tight loop.
    hass.states.async_set(entity, "0.06", {"unit_of_measurement": "EUR/kWh"})
    await hass.async_block_till_done()
    assert len(asked) == 2


async def test_hacs_nordpool_while_down_is_not_taken_for_the_built_in_one(
    hass, freezer
) -> None:
    """Same domain, no attributes, no service: unavailable, not a failed call."""
    freezer.move_to(NOW)
    entity = _registered(hass, "nordpool", "nordpool_kwh_fi_eur_3_095_0")
    hass.states.async_set(entity, "unavailable", {})
    entry, _ = await _setup(
        hass, "FI", _api_entries(6, 0.30, "forecast"), {"own_price_entity": entity}
    )
    assert entry.runtime_data.own_source.status == "unavailable"
    assert all(source == "forecast" for _, _, source in _values(entry))


async def _options(hass, entry, entity: str | None):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    if entity is None:
        return result, await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={}
        )
    return result, await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"own_price_entity": entity}
    )


async def test_the_dialog_names_the_benefit_by_market(hass, freezer) -> None:
    freezer.move_to(NOW)
    finland, _ = await _setup(hass, "FI", _api_entries(6, 0.30, "forecast"))
    result = await hass.config_entries.options.async_init(finland.entry_id)
    assert result["step_id"] == "own_source_forecast_only"

    germany, _ = await _setup(hass, "DE", _api_entries(6, 0.10, "day_ahead"))
    result = await hass.config_entries.options.async_init(germany.entry_id)
    assert result["step_id"] == "own_source"


async def test_vat_in_the_source_is_caught_and_can_be_overruled(hass, freezer) -> None:
    """Germany has published prices, so the check compares exactly."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    entity = _registered(hass, "entsoe", "entsoe_avg_price")
    prices = [
        {"time": str(START + timedelta(hours=i)), "price": round(0.10 * 1.19, 5)}
        for i in range(6)
    ]
    hass.states.async_set(
        entity, "0.119", {"unit_of_measurement": "€/kWh", "prices_today": prices}
    )
    entry, _ = await _setup(hass, "DE", _api_entries(6, 0.10, "day_ahead"))

    with (
        patch(f"{API}.async_get_summary", new=AsyncMock(return_value=SUMMARY)),
        patch(
            f"{API}.async_get_prices",
            new=AsyncMock(return_value=_prices(_api_entries(6, 0.10, "day_ahead"))),
        ),
    ):
        _, shown = await _options(hass, entry, entity)
        assert shown["type"] is FlowResultType.FORM
        assert shown["errors"] == {"base": "own_published_mismatch"}

        saved = await hass.config_entries.options.async_configure(
            shown["flow_id"], user_input={"own_price_entity": entity}
        )
        await hass.async_block_till_done()
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"own_price_entity": entity}


async def test_a_clean_source_is_saved_at_once_and_an_empty_field_clears_it(
    hass, freezer
) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    entity = _registered(hass, "entsoe", "entsoe_avg_price")
    prices = [{"time": str(START + timedelta(hours=i)), "price": 0.10} for i in range(6)]
    hass.states.async_set(
        entity, "0.10", {"unit_of_measurement": "€/kWh", "prices_today": prices}
    )
    entry, _ = await _setup(hass, "DE", _api_entries(6, 0.10, "day_ahead"))

    with (
        patch(f"{API}.async_get_summary", new=AsyncMock(return_value=SUMMARY)),
        patch(
            f"{API}.async_get_prices",
            new=AsyncMock(return_value=_prices(_api_entries(6, 0.10, "day_ahead"))),
        ),
    ):
        _, saved = await _options(hass, entry, entity)
        await hass.async_block_till_done()
        assert saved["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options == {"own_price_entity": entity}

        _, cleared = await _options(hass, entry, None)
        await hass.async_block_till_done()
    assert cleared["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {}


async def test_a_currency_that_does_not_fit_cannot_be_overruled(hass, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Helsinki")
    freezer.move_to(NOW)
    entity = _registered(hass, "nordpool", "nordpool_kwh_fi_sek_3_095_0")
    hass.states.async_set(
        entity,
        "1.0",
        {"unit_of_measurement": "SEK/kWh", "raw_today": _raw(HELSINKI_MIDNIGHT, [1.0] * 24)},
    )
    entry, _ = await _setup(hass, "FI", _api_entries(6, 0.30, "forecast"))

    _, shown = await _options(hass, entry, entity)
    assert shown["errors"] == {"base": "own_currency_mismatch"}
    assert shown["description_placeholders"] == {
        "source_unit": "SEK/kWh",
        "series_unit": "EUR/kWh",
    }
    again = await hass.config_entries.options.async_configure(
        shown["flow_id"], user_input={"own_price_entity": entity}
    )
    assert again["type"] is FlowResultType.FORM
    assert again["errors"] == {"base": "own_currency_mismatch"}
    assert entry.options == {}


async def test_a_sensor_without_a_price_list_is_refused(hass, freezer) -> None:
    freezer.move_to(NOW)
    entity = _registered(hass, "entsoe", "entsoe_current_price")
    hass.states.async_set(entity, "0.10", {"unit_of_measurement": "€/kWh"})
    entry, _ = await _setup(hass, "DE", _api_entries(6, 0.10, "day_ahead"))
    _, shown = await _options(hass, entry, entity)
    assert shown["errors"] == {"base": "own_unknown_format"}
