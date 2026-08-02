"""Shared entity base for Energy Price Forecast EU."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MARKET, DOMAIN, NAME
from .coordinator import EnergyPriceForecastCoordinator


class EnergyPriceForecastEntity(CoordinatorEntity[EnergyPriceForecastCoordinator]):
    """Base entity tied to one market coordinator."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyPriceForecastCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        market = entry.data[CONF_MARKET]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=NAME,
            name=f"{NAME} {market}",
            model="Forecast service",
            configuration_url="https://energypriceforecast.eu/",
        )
