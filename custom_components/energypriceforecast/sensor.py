"""Sensor entities for Energy Price Forecast EU."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EnergyPriceForecastCoordinator
from .entity import EnergyPriceForecastEntity


def _path(data: dict[str, Any], *parts: str) -> Any:
    value: Any = data
    for part in parts:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, kw_only=True)
class EnergyPriceForecastSensorDescription(SensorEntityDescription):
    """Describe how a value is read from the summary."""

    value_fn: Callable[[dict[str, Any]], Any]
    unit_fn: Callable[[dict[str, Any]], str | None] | None = None


SENSORS: tuple[EnergyPriceForecastSensorDescription, ...] = (
    EnergyPriceForecastSensorDescription(
        key="current_price",
        translation_key="current_price",
        icon="mdi:cash",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda data: _path(data, "flat", "current_price"),
        unit_fn=lambda data: _path(data, "flat", "current_price_unit"),
    ),
    EnergyPriceForecastSensorDescription(
        key="current_co2",
        translation_key="current_co2",
        icon="mdi:molecule-co2",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="gCO2/kWh",
        suggested_display_precision=1,
        value_fn=lambda data: _path(data, "flat", "current_co2_g_kwh"),
    ),
    EnergyPriceForecastSensorDescription(
        key="cheapest_window_average_price",
        translation_key="cheapest_window_average_price",
        icon="mdi:cash-clock",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda data: _path(data, "flat", "best_price_window_avg_price"),
        unit_fn=lambda data: _path(data, "flat", "current_price_unit"),
    ),
    EnergyPriceForecastSensorDescription(
        key="cheapest_window_start",
        translation_key="cheapest_window_start",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _timestamp(
            _path(data, "flat", "best_price_window_start")
        ),
    ),
    EnergyPriceForecastSensorDescription(
        key="cheapest_window_end",
        translation_key="cheapest_window_end",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _timestamp(_path(data, "flat", "best_price_window_end")),
    ),
    EnergyPriceForecastSensorDescription(
        key="greenest_window_average_co2",
        translation_key="greenest_window_average_co2",
        icon="mdi:leaf-clock",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="gCO2/kWh",
        suggested_display_precision=1,
        value_fn=lambda data: _path(data, "flat", "best_co2_window_avg_g_co2_kwh"),
    ),
    EnergyPriceForecastSensorDescription(
        key="greenest_window_start",
        translation_key="greenest_window_start",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _timestamp(_path(data, "flat", "best_co2_window_start")),
    ),
    EnergyPriceForecastSensorDescription(
        key="greenest_window_end",
        translation_key="greenest_window_end",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _timestamp(_path(data, "flat", "best_co2_window_end")),
    ),
    EnergyPriceForecastSensorDescription(
        key="combined_window_score",
        translation_key="combined_window_score",
        icon="mdi:chart-bell-curve-cumulative",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda data: _path(data, "flat", "combined_window_score"),
    ),
    EnergyPriceForecastSensorDescription(
        key="allowed_horizon",
        translation_key="allowed_horizon",
        icon="mdi:clock-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda data: _path(data, "meta", "allowed_horizon_hours"),
    ),
    EnergyPriceForecastSensorDescription(
        key="used_horizon",
        translation_key="used_horizon",
        icon="mdi:clock-fast",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement=UnitOfTime.HOURS,
        value_fn=lambda data: _path(data, "meta", "used_horizon_hours"),
    ),
    EnergyPriceForecastSensorDescription(
        key="api_key_state",
        translation_key="api_key_state",
        icon="mdi:key-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _path(data, "meta", "api_key_state"),
    ),
    EnergyPriceForecastSensorDescription(
        key="last_update",
        translation_key="last_update",
        icon="mdi:update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _timestamp(data.get("generated_at")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from one config entry."""
    coordinator: EnergyPriceForecastCoordinator = entry.runtime_data
    async_add_entities(
        EnergyPriceForecastSensor(coordinator, entry, description)
        for description in SENSORS
    )


class EnergyPriceForecastSensor(EnergyPriceForecastEntity, SensorEntity):
    """One sensor backed by the shared summary response."""

    entity_description: EnergyPriceForecastSensorDescription

    def __init__(
        self,
        coordinator: EnergyPriceForecastCoordinator,
        entry: ConfigEntry,
        description: EnergyPriceForecastSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn is not None:
            return self.entity_description.unit_fn(self.coordinator.data)
        return self.entity_description.native_unit_of_measurement
