"""Binary sensors for Energy Price Forecast EU."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
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


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    entities: list[BinarySensorEntity] = [
        EnergyPriceForecastBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    ]
    if coordinator.retail_pricing:
        entities.append(EnergyPriceForecastRetailWindowBinarySensor(coordinator, entry))
    if coordinator.cheapest_hours_count > 0:
        entities.append(EnergyPriceForecastCheapestHoursBinarySensor(coordinator, entry))
    if coordinator.weekend_hours_count > 0:
        entities.append(EnergyPriceForecastWeekendHoursBinarySensor(coordinator, entry))
    if coordinator.greenest_hours_count > 0:
        entities.append(EnergyPriceForecastGreenestHoursBinarySensor(coordinator, entry))
    entities.append(EnergyPriceForecastCombinedWindowBinarySensor(coordinator, entry))
    async_add_entities(entities)


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


class EnergyPriceForecastRetailWindowBinarySensor(
    EnergyPriceForecastEntity, BinarySensorEntity
):
    """On while now falls inside the cheapest window, in retail terms.

    Only created when retail pricing was enabled. Backed by
    coordinator.retail_summary rather than the shared (spot-price)
    summary response, so this can differ from the base
    cheapest_window_active sensor if retail components shift which
    window is actually cheapest.
    """

    _attr_translation_key = "retail_cheapest_window_active"
    _attr_icon = "mdi:cash-clock"

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "retail_cheapest_window_active")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.retail_summary is not None

    @property
    def is_on(self) -> bool:
        if self.coordinator.retail_summary is None:
            return False
        return bool(
            self.coordinator.retail_summary.get("flat", {}).get(
                "is_cheapest_window_now", False
            )
        )


class EnergyPriceForecastCheapestHoursBinarySensor(
    EnergyPriceForecastEntity, BinarySensorEntity
):
    """On while now falls inside one of the N cheapest upcoming hours.

    Only created when a positive hour count was configured. Backed by
    coordinator.cheapest_hours rather than the shared summary response.
    """

    _attr_translation_key = "is_in_cheapest_hours"
    _attr_icon = "mdi:sort-clock-ascending"

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "is_in_cheapest_hours")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.cheapest_hours is not None

    @property
    def is_on(self) -> bool:
        now = datetime.now(timezone.utc)
        return any(
            hour["start"] <= now < hour["end"]
            for hour in self.coordinator.cheapest_hours or []
        )


class EnergyPriceForecastWeekendHoursBinarySensor(
    EnergyPriceForecastEntity, BinarySensorEntity
):
    """On while now falls inside one of this weekend's N cheapest hours.

    Only created when a positive weekend hour count was configured.
    Backed by coordinator.weekend_hours, the independently-locked
    Saturday-to-Monday plan (see planning.fixed_weekend_window).
    """

    _attr_translation_key = "is_in_weekend_hours"
    _attr_icon = "mdi:calendar-weekend"

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "is_in_weekend_hours")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.weekend_hours is not None

    @property
    def is_on(self) -> bool:
        now = datetime.now(timezone.utc)
        return any(
            hour["start"] <= now < hour["end"]
            for hour in self.coordinator.weekend_hours or []
        )


class EnergyPriceForecastGreenestHoursBinarySensor(
    EnergyPriceForecastEntity, BinarySensorEntity
):
    """Whether one of the plan's cleanest hours is running right now.

    The switching signal for a CO2-driven automation, the counterpart of the
    cheapest-hours sensor. Backed by coordinator.greenest_hours.
    """

    _attr_translation_key = "is_in_greenest_hours"
    _attr_icon = "mdi:leaf-circle-outline"

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "is_in_greenest_hours")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.greenest_hours is not None

    @property
    def is_on(self) -> bool:
        now = datetime.now(timezone.utc)
        return any(
            hour["start"] <= now < hour["end"]
            for hour in self.coordinator.greenest_hours or []
        )


class EnergyPriceForecastCombinedWindowBinarySensor(
    EnergyPriceForecastEntity, BinarySensorEntity
):
    """Whether the best price-and-CO2 compromise window is running now.

    The API reports no "is active now" flag for this window the way it does
    for the price and CO2 ones, so this reads its start and end. The window
    is a single contiguous block, so comparing against now is the whole test.
    """

    _attr_translation_key = "combined_window_active"
    _attr_icon = "mdi:scale-balance"

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "combined_window_active")

    def _window(self) -> tuple[datetime | None, datetime | None]:
        flat = (self.coordinator.data or {}).get("flat") or {}
        return (
            _parse_timestamp(flat.get("combined_window_start")),
            _parse_timestamp(flat.get("combined_window_end")),
        )

    @property
    def available(self) -> bool:
        start, end = self._window()
        return super().available and start is not None and end is not None

    @property
    def is_on(self) -> bool:
        start, end = self._window()
        if start is None or end is None:
            return False
        return start <= datetime.now(timezone.utc) < end
