"""Binary sensors for Energy Price Forecast EU."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EnergyPriceForecastCoordinator
from .entity import EnergyPriceForecastEntity


@dataclass(frozen=True, kw_only=True)
class EnergyPriceForecastBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Boolean value in the flat summary."""

    value_fn: Callable[[dict[str, Any]], bool]


BINARY_SENSORS: tuple[EnergyPriceForecastBinarySensorDescription, ...] = (
    EnergyPriceForecastBinarySensorDescription(
        key="cheapest_window_active",
        translation_key="cheapest_window_active",
        icon="mdi:cash-clock",
        value_fn=lambda data: bool(
            data.get("flat", {}).get("is_cheapest_window_now", False)
        ),
    ),
    EnergyPriceForecastBinarySensorDescription(
        key="greenest_window_active",
        translation_key="greenest_window_active",
        icon="mdi:leaf-clock",
        value_fn=lambda data: bool(
            data.get("flat", {}).get("is_greenest_window_now", False)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors from one config entry."""
    coordinator: EnergyPriceForecastCoordinator = entry.runtime_data
    async_add_entities(
        EnergyPriceForecastBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class EnergyPriceForecastBinarySensor(EnergyPriceForecastEntity, BinarySensorEntity):
    """One Boolean sensor backed by the shared summary response."""

    entity_description: EnergyPriceForecastBinarySensorDescription

    def __init__(
        self,
        coordinator: EnergyPriceForecastCoordinator,
        entry: ConfigEntry,
        description: EnergyPriceForecastBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.coordinator.data)
