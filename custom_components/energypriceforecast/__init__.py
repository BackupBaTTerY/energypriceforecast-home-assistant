"""Energy Price Forecast EU integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DEFAULT_GREENEST_HOURS_COUNT,
    DEFAULT_WEEKEND_HOURS_COUNT,
    MAX_HORIZON_HOURS,
    PLATFORMS,
    PRICES_API_URL,
    RETAIL_SOURCE_ESTIMATE,
    RETAIL_SOURCE_OFF,
)
from .coordinator import EnergyPriceForecastCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one configured market."""
    # An entry saved before the option existed has no such key, which reads
    # as off: exactly the currency it was getting before.
    currency = requested_currency(
        entry.data[CONF_MARKET],
        entry.data.get(CONF_LOCAL_CURRENCY, DEFAULT_LOCAL_CURRENCY),
    )
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
        estimate = data.pop(CONF_RETAIL_PRICING, False)
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
