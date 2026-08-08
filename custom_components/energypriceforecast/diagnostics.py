"""Diagnostics for Energy Price Forecast EU."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_POSTAL_CODE
from .coordinator import EnergyPriceForecastCoordinator

TO_REDACT = {CONF_API_KEY, CONF_POSTAL_CODE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted config and the latest public API response."""
    coordinator: EnergyPriceForecastCoordinator = entry.runtime_data
    return {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "data": coordinator.data,
        "retail_data": coordinator.retail_data,
        "cheapest_hours": coordinator.cheapest_hours,
        "last_update_success": coordinator.last_update_success,
    }
