"""Energy Price Forecast EU integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, tzinfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import EnergyPriceForecastApi, requested_currency
from .const import (
    CONF_API_KEY,
    CONF_CHEAPEST_HOURS_COUNT,
    CONF_CHEAPEST_HOURS_START_HOUR,
    CONF_CHEAPEST_HOURS_WINDOW_HOURS,
    CONF_HORIZON_HOURS,
    CONF_LOCAL_CURRENCY,
    CONF_MARKET,
    CONF_POSTAL_CODE,
    CONF_RETAIL_FACTOR,
    CONF_RETAIL_PRICING,
    CONF_RETAIL_SOURCE,
    CONF_RETAIL_SURCHARGE,
    CONF_TOU_SOURCE,
    CONF_TOU_TARIFF,
    CONF_UPDATE_INTERVAL_MINUTES,
    CONF_GREENEST_HOURS_COUNT,
    CONF_WEEKEND_HOURS_COUNT,
    CONF_WINDOW_HOURS,
    DEFAULT_API_URL,
    DEFAULT_CHEAPEST_HOURS_COUNT,
    DEFAULT_CHEAPEST_HOURS_START_HOUR,
    DEFAULT_CHEAPEST_HOURS_WINDOW_HOURS,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_LOCAL_CURRENCY,
    DEFAULT_RETAIL_FACTOR,
    DEFAULT_RETAIL_SURCHARGE,
    DEFAULT_TIME_ZONE,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DEFAULT_GREENEST_HOURS_COUNT,
    DEFAULT_WEEKEND_HOURS_COUNT,
    MARKET_TIME_ZONES,
    MAX_HORIZON_HOURS,
    PLATFORMS,
    PRICES_API_URL,
    RETAIL_SOURCE_ESTIMATE,
    RETAIL_SOURCE_OFF,
    TOU_SOURCE_DATAHUB,
    TOU_SOURCE_MANUAL,
    TOU_SOURCE_OFF,
)
from .coordinator import EnergyPriceForecastCoordinator
from .dk_datahub import DatahubError, async_load_rates
from .time_of_use import HourlyTable, TariffSource, schedule_from_config

_LOGGER = logging.getLogger(__name__)


async def _async_tariff(
    hass: HomeAssistant, entry: ConfigEntry
) -> tuple[
    TariffSource | None,
    Callable[[date], Awaitable[TariffSource | None]] | None,
    tzinfo,
]:
    """The network tariff this entry prices with, and its market's clock.

    A schedule the user typed in never changes on its own, so it is built
    once here. Denmark's published tariff does change - three times a year -
    so what is built here is a loader the coordinator calls once a day.
    """
    market = entry.data[CONF_MARKET]
    zone = (
        await dt_util.async_get_time_zone(
            MARKET_TIME_ZONES.get(market, DEFAULT_TIME_ZONE)
        )
        or dt_util.UTC
    )
    source = entry.data.get(CONF_TOU_SOURCE, TOU_SOURCE_OFF)
    if source == TOU_SOURCE_MANUAL:
        return schedule_from_config(entry.data, zone), None, zone
    if source == TOU_SOURCE_DATAHUB:
        choice = str(entry.data.get(CONF_TOU_TARIFF) or "")
        session = async_get_clientsession(hass)
        horizon = int(entry.data.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS))
        # Every hour of the forecast needs a rate, and the horizon is given
        # in hours from now, so it can reach into the day after tomorrow.
        days = -(-horizon // 24) + 1

        async def _load(day: date) -> TariffSource | None:
            try:
                rates = await async_load_rates(session, choice, day, days)
            except DatahubError as err:
                # The tariffs are published months ahead, so the table loaded
                # yesterday still covers today: a failed reload is a line in
                # the log, not something the user has to act on.
                _LOGGER.warning("Danish grid tariff could not be updated: %s", err)
                return None
            return HourlyTable(prices=rates, zone=zone, name=choice)

        return None, _load, zone
    return None, None, zone


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one configured market."""
    # An entry saved before the option existed has no such key, which reads
    # as off: exactly the currency it was getting before.
    currency = requested_currency(
        entry.data[CONF_MARKET],
        entry.data.get(CONF_LOCAL_CURRENCY, DEFAULT_LOCAL_CURRENCY),
    )
    tariff, tariff_loader, zone = await _async_tariff(hass, entry)
    api = EnergyPriceForecastApi(
        session=async_get_clientsession(hass),
        base_url=DEFAULT_API_URL,
        prices_url=PRICES_API_URL,
        market=entry.data[CONF_MARKET],
        horizon_hours=entry.data[CONF_HORIZON_HOURS],
        window_hours=entry.data[CONF_WINDOW_HOURS],
        api_key=entry.data.get(CONF_API_KEY),
        currency=currency,
    )
    coordinator = EnergyPriceForecastCoordinator(
        hass,
        api,
        entry_id=entry.entry_id,
        retail_source=entry.data.get(CONF_RETAIL_SOURCE, RETAIL_SOURCE_OFF),
        retail_factor=entry.data.get(CONF_RETAIL_FACTOR, DEFAULT_RETAIL_FACTOR),
        retail_surcharge=entry.data.get(
            CONF_RETAIL_SURCHARGE, DEFAULT_RETAIL_SURCHARGE
        ),
        postal_code=entry.data.get(CONF_POSTAL_CODE),
        update_interval_minutes=entry.data.get(
            CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES
        ),
        cheapest_hours_count=entry.data.get(
            CONF_CHEAPEST_HOURS_COUNT, DEFAULT_CHEAPEST_HOURS_COUNT
        ),
        cheapest_hours_window_hours=entry.data.get(
            CONF_CHEAPEST_HOURS_WINDOW_HOURS, DEFAULT_CHEAPEST_HOURS_WINDOW_HOURS
        ),
        cheapest_hours_start_hour=entry.data.get(
            CONF_CHEAPEST_HOURS_START_HOUR, DEFAULT_CHEAPEST_HOURS_START_HOUR
        ),
        weekend_hours_count=entry.data.get(
            CONF_WEEKEND_HOURS_COUNT, DEFAULT_WEEKEND_HOURS_COUNT
        ),
        greenest_hours_count=entry.data.get(
            CONF_GREENEST_HOURS_COUNT, DEFAULT_GREENEST_HOURS_COUNT
        ),
        currency=currency,
        tariff=tariff,
        tariff_loader=tariff_loader,
        zone=zone,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring an older config entry up to the current schema."""
    if entry.version == 1:
        data = dict(entry.data)
        horizon = data.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS)
        try:
            horizon = int(horizon)
        except (TypeError, ValueError):
            horizon = DEFAULT_HORIZON_HOURS
        # 168 was offered as a horizon for a while, but the forecast never
        # produced more than MAX_HORIZON_HOURS of data, so the extra hours
        # never arrived. Clamping here keeps the stored value in step with
        # what the config flow now offers - an entry left at 168 would make
        # the reconfigure form raise on a value no longer in the dropdown.
        data[CONF_HORIZON_HOURS] = min(horizon, MAX_HORIZON_HOURS)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    if entry.version == 2:
        data = dict(entry.data)
        # 1.6.0 turned the retail checkbox into a choice between the API's
        # estimate and the user's own formula. A ticked box always meant the
        # estimate, and that is what it becomes - nothing about the prices, the
        # entities or the stored plans changes for whoever had it on.
        # The box itself stays until the next reconfigure. Nothing here reads
        # it again, but if 1.6.0 has to be rolled back, 1.5.x still finds it
        # and keeps the retail price on instead of dropping it without a word.
        estimate = data.get(CONF_RETAIL_PRICING, False)
        data.setdefault(
            CONF_RETAIL_SOURCE,
            RETAIL_SOURCE_ESTIMATE if estimate else RETAIL_SOURCE_OFF,
        )
        hass.config_entries.async_update_entry(entry, data=data, version=3)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one configured market."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
