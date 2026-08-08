"""Energy Price Forecast EU integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnergyPriceForecastApi
from .const import (
    CONF_API_KEY,
    CONF_HORIZON_HOURS,
    CONF_MARKET,
    CONF_POSTAL_CODE,
    CONF_RETAIL_PRICING,
    CONF_WINDOW_HOURS,
    DEFAULT_API_URL,
    PLATFORMS,
    PRICES_API_URL,
)
from .coordinator import EnergyPriceForecastCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one configured market."""
    api = EnergyPriceForecastApi(
        session=async_get_clientsession(hass),
        base_url=DEFAULT_API_URL,
        prices_url=PRICES_API_URL,
        market=entry.data[CONF_MARKET],
        horizon_hours=entry.data[CONF_HORIZON_HOURS],
        window_hours=entry.data[CONF_WINDOW_HOURS],
        api_key=entry.data.get(CONF_API_KEY),
    )
    coordinator = EnergyPriceForecastCoordinator(
        hass,
        api,
        retail_pricing=entry.data.get(CONF_RETAIL_PRICING, False),
        postal_code=entry.data.get(CONF_POSTAL_CODE),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one configured market."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
