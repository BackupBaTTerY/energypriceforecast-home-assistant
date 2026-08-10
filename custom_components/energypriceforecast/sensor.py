"""Sensor entities for Energy Price Forecast EU."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from homeassistant.util import dt as dt_util

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


def _current_entry(
    series_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the price entry covering now, or the earliest one as fallback.

    Works for any {"entries": [...]} payload from the prices endpoint,
    whether that's the retail series or the base price series.
    """
    entries = _path(series_data or {}, "entries") if series_data else None
    if not isinstance(entries, list) or not entries:
        return None
    now = datetime.now(timezone.utc)
    for entry in entries:
        start = _timestamp(entry.get("start"))
        end = _timestamp(entry.get("end"))
        if start is not None and end is not None and start <= now < end:
            return entry
    return entries[0]


def _split_today_tomorrow(
    entries: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split raw price entries into today's and tomorrow's, in local time.

    Matches the raw_today/raw_tomorrow attribute convention used by the
    Nordpool integration, so existing apexcharts-card templates work
    with minimal changes. Only contains the hours actually returned by
    the API - a rolling window starting at "now" - not the full
    calendar day; hours of today that have already passed are not
    included since the API does not look backward from local midnight.
    """
    if not isinstance(entries, list):
        return [], []
    local_today = dt_util.now().date()
    local_tomorrow = local_today + timedelta(days=1)
    today: list[dict[str, Any]] = []
    tomorrow: list[dict[str, Any]] = []
    for entry in entries:
        start = _timestamp(entry.get("start"))
        if start is None:
            continue
        item = {
            "start": entry.get("start"),
            "end": entry.get("end"),
            "value": entry.get("value"),
        }
        local_date = dt_util.as_local(start).date()
        if local_date == local_today:
            today.append(item)
        elif local_date == local_tomorrow:
            tomorrow.append(item)
    return today, tomorrow


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
        icon="mdi:leaf-clock",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _timestamp(_path(data, "flat", "best_co2_window_start")),
    ),
    EnergyPriceForecastSensorDescription(
        key="greenest_window_end",
        translation_key="greenest_window_end",
        icon="mdi:leaf-clock",
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
    entities: list[SensorEntity] = [
        EnergyPriceForecastSensor(coordinator, entry, description)
        for description in SENSORS
    ]
    entities.append(EnergyPriceForecastPriceSeriesSensor(coordinator, entry))
    if coordinator.retail_pricing:
        entities.append(EnergyPriceForecastRetailPriceSensor(coordinator, entry))
    if coordinator.cheapest_hours_count > 0:
        entities.append(EnergyPriceForecastCheapestHoursSensor(coordinator, entry))
    async_add_entities(entities)


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


class EnergyPriceForecastRetailPriceSensor(EnergyPriceForecastEntity, SensorEntity):
    """Current assumption-based retail (all-in) electricity price.

    Only created when retail pricing was enabled during setup. Backed by
    coordinator.retail_data rather than the shared summary response.
    State mirrors the current retail price; raw_today/raw_tomorrow
    attributes carry the full retail series, same shape as
    EnergyPriceForecastPriceSeriesSensor but with retail values.
    """

    _attr_translation_key = "retail_current_price"
    _attr_icon = "mdi:cash-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "retail_current_price")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.retail_data is not None

    @property
    def native_value(self) -> Any:
        current = _current_entry(self.coordinator.retail_data)
        return current.get("value") if current else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _path(self.coordinator.retail_data or {}, "unit")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entries = _path(self.coordinator.retail_data or {}, "entries")
        today, tomorrow = _split_today_tomorrow(entries)
        return {"raw_today": today, "raw_tomorrow": tomorrow}


class EnergyPriceForecastPriceSeriesSensor(EnergyPriceForecastEntity, SensorEntity):
    """Raw price forecast series for charting and custom automations.

    Always created (unlike the other optional sensors): forecasting the
    price series is this integration's core purpose, not a niche
    add-on. State mirrors the current market price; raw_today/
    raw_tomorrow attributes carry the full series. Backed by
    coordinator.price_series rather than the shared summary response.
    """

    _attr_translation_key = "price_series"
    _attr_icon = "mdi:chart-line"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "price_series")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.price_series is not None

    @property
    def native_value(self) -> Any:
        current = _current_entry(self.coordinator.price_series)
        return current.get("value") if current else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _path(self.coordinator.price_series or {}, "unit")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entries = _path(self.coordinator.price_series or {}, "entries")
        today, tomorrow = _split_today_tomorrow(entries)
        return {"raw_today": today, "raw_tomorrow": tomorrow}


class EnergyPriceForecastCheapestHoursSensor(EnergyPriceForecastEntity, SensorEntity):
    """Start of the next of the N cheapest upcoming hours.

    The hours may be non-contiguous, unlike the API's single best
    continuous window. Only created when a positive hour count was
    configured. Backed by coordinator.cheapest_hours.
    """

    _attr_translation_key = "cheapest_hours_next_start"
    _attr_icon = "mdi:sort-clock-ascending"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "cheapest_hours_next_start")

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.cheapest_hours)

    @property
    def native_value(self) -> Any:
        hours = self.coordinator.cheapest_hours
        return hours[0]["start"] if hours else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        hours = self.coordinator.cheapest_hours or []
        return {
            "hours": [
                {
                    "start": hour["start"].isoformat(),
                    "end": hour["end"].isoformat(),
                    "average_value": round(hour["average_value"], 4),
                }
                for hour in hours
            ],
        }
