"""Diagnostics for Energy Price Forecast EU."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_API_KEY, CONF_POSTAL_CODE, VERSION
from .coordinator import EnergyPriceForecastCoordinator
from .health import series_continuity

TO_REDACT = {CONF_API_KEY, CONF_POSTAL_CODE}


def _entries(series: Any) -> Any:
    return series.get("entries") if isinstance(series, dict) else None


def _continuity(coordinator: Any) -> dict[str, Any]:
    """Gaps and overlaps of every series this entry holds.

    A missing or doubled hour around a daylight-saving change is the kind of
    fault a user can only describe as "the chart looks wrong"; this names
    the times instead.
    """
    report = {
        "price_series": series_continuity(
            _entries(getattr(coordinator, "price_series", None))
        ),
    }
    retail = getattr(coordinator, "retail_data", None)
    if retail is not None:
        report["retail_series"] = series_continuity(_entries(retail))
    co2 = getattr(coordinator, "co2_series", None)
    if co2 is not None:
        report["co2_series"] = series_continuity(_entries(co2))
    return report


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted config and the latest public API response."""
    coordinator: EnergyPriceForecastCoordinator = entry.runtime_data
    health = getattr(coordinator, "health", None)
    return {
        "integration_version": VERSION,
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "data": coordinator.data,
        "retail_data": coordinator.retail_data,
        "cheapest_hours": coordinator.cheapest_hours,
        "cheapest_hours_window_average": coordinator.cheapest_hours_window_average,
        "weekend_hours": coordinator.weekend_hours,
        "weekend_hours_window_average": coordinator.weekend_hours_window_average,
        "last_update_success": coordinator.last_update_success,
        "sources": health.snapshot(dt_util.utcnow()) if health is not None else {},
        "series_continuity": _continuity(coordinator),
    }
