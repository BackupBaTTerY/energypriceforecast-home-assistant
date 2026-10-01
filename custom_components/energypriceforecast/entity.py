"""Shared entity base for Energy Price Forecast EU."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_utc_time_change
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


class QuarterHourStateRefreshMixin:
    """Re-render an entity at every price-slot boundary.

    Everything this integration says about *now* - the current price, the
    current CO2 intensity, a countdown, whether a planned hour is running -
    is worked out from cached data at the moment the entity renders. Between
    two polls nothing renders it, so without this the value stays as it was
    at the last poll: by default half an hour late, and up to two hours with
    the slowest update interval. Prices change on the quarter hour, so that
    is when these entities update.

    Entities whose value cannot change between polls - the horizon, the
    API-key state, a stored plan's average - say so by overriding
    ``_follows_slot_boundaries``, and are left alone.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not self._follows_slot_boundaries():
            return
        self.async_on_remove(
            async_track_utc_time_change(
                self.hass,
                self._handle_slot_change,
                minute=(0, 15, 30, 45),
                second=0,
            )
        )

    def _follows_slot_boundaries(self) -> bool:
        """Whether this entity's value depends on when it is read."""
        return True

    @callback
    def _handle_slot_change(self, _now: datetime) -> None:
        self.async_write_ha_state()
