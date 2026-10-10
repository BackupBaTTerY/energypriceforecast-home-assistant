"""Binary sensors for Energy Price Forecast EU."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import EnergyPriceForecastCoordinator
from .entity import EnergyPriceForecastEntity, QuarterHourStateRefreshMixin


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _window_is_active(
    flat: dict[str, Any], start_key: str, end_key: str, fallback_key: str
) -> bool:
    """Whether now falls inside the window, decided by its own timestamps.

    The summary's "is active now" flag was true when the API answered, which
    by the next poll can be half an hour ago - long enough for the window to
    have started or ended in between. The timestamps do not move, so the
    flag is worked out from them at every slot boundary, and the flag itself
    is only used when they are missing.

    Mind which window: the API sets these flags for its *best* window, not
    for the one the start and end sensors show.
    """
    start = _parse_timestamp(flat.get(start_key))
    end = _parse_timestamp(flat.get(end_key))
    if start is None or end is None:
        return bool(flat.get(fallback_key, False))
    return start <= datetime.now(timezone.utc) < end


def _entry_covering_now(
    series_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the entry of a prices payload whose slot covers this moment.

    Strictly the covering slot, with no nearest-neighbour fallback: asking
    whether the price being paid right now is published must not be
    answered with a neighbouring slot's provenance.
    """
    entries = (series_data or {}).get("entries")
    if not isinstance(entries, list):
        return None
    now = datetime.now(timezone.utc)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start = _parse_timestamp(entry.get("start"))
        end = _parse_timestamp(entry.get("end"))
        if start is not None and end is not None and start <= now < end:
            return entry
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
        value_fn=lambda data: _window_is_active(
            (data or {}).get("flat") or {},
            "best_price_window_start",
            "best_price_window_end",
            "is_cheapest_window_now",
        ),
    ),
    EnergyPriceForecastBinarySensorDescription(
        key="greenest_window_active",
        translation_key="greenest_window_active",
        icon="mdi:leaf-clock",
        value_fn=lambda data: _window_is_active(
            (data or {}).get("flat") or {},
            "best_co2_window_start",
            "best_co2_window_end",
            "is_greenest_window_now",
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
    if coordinator.expensive_hours_count > 0:
        entities.append(
            EnergyPriceForecastExpensiveHoursBinarySensor(coordinator, entry)
        )
    if coordinator.greenest_hours_count > 0:
        entities.append(EnergyPriceForecastGreenestHoursBinarySensor(coordinator, entry))
    entities.append(EnergyPriceForecastCombinedWindowBinarySensor(coordinator, entry))
    entities.append(EnergyPriceForecastUnpublishedPriceBinarySensor(coordinator, entry))
    async_add_entities(entities)


class EnergyPriceForecastBinarySensor(
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
):
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
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
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
        return _window_is_active(
            self.coordinator.retail_summary.get("flat") or {},
            "best_price_window_start",
            "best_price_window_end",
            "is_cheapest_window_now",
        )


class EnergyPriceForecastCheapestHoursBinarySensor(
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
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
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
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


class EnergyPriceForecastExpensiveHoursBinarySensor(
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
):
    """Whether one of the N dearest planned hours is running right now.

    The inverse trigger of the cheapest-hours flag: switch a load off, or a
    battery to discharging. Backed by coordinator.expensive_hours.
    """

    _attr_translation_key = "is_in_expensive_hours"
    _attr_icon = "mdi:cash-remove"

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "is_in_expensive_hours")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.expensive_hours is not None

    @property
    def is_on(self) -> bool:
        now = datetime.now(timezone.utc)
        return any(
            hour["start"] <= now < hour["end"]
            for hour in self.coordinator.expensive_hours or []
        )


class EnergyPriceForecastGreenestHoursBinarySensor(
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
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


class EnergyPriceForecastUnpublishedPriceBinarySensor(
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
):
    """On while the price for the slot running now is only a forecast.

    Deliberately narrow. Beyond the published window the series is always a
    forecast - that is the product, and a flag reporting it would be on
    every afternoon and read by nobody. The price for *now*, by contrast,
    was settled at auction yesterday, so a forecast covering the current
    slot means the published prices never arrived. That happened on
    2026-10-09/10 for NL, GR, RO and SK, where neither upstream platform
    had the delivery day.

    A problem flag rather than a repair issue, because there is nothing the
    owner of this installation can fix. The entities stay available on
    purpose: the numbers are there, they are only estimates, and an
    automation that stops dead helps nobody. What such an automation can do
    is widen its margin or wait, and for that it needs this one Boolean;
    how far the published prices reach is in the attributes.

    An early-auction price does not raise it. That is not the official
    price either, but it is a traded one: measured against the NL
    day-ahead result for 2026-10-10 it sat 0.94 ct/kWh away where the model
    forecast sat 3.18 ct away. The attribute still names the source, so a
    template can tell the three cases apart.
    """

    _attr_translation_key = "prices_unpublished"
    # No icon on purpose: the problem device class brings its own pair, and
    # Home Assistant then renders the state as Problem/OK and colours it,
    # which is the entire point of the entity.
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "prices_unpublished")

    def _current_source(self) -> str | None:
        """The provenance of the slot covering now, series first.

        The series carries it per slot; the summary's own field is the
        fallback for a response that arrived without a series.
        """
        entry = _entry_covering_now(self.coordinator.price_series)
        if entry is not None:
            source = entry.get("source")
            if isinstance(source, str) and source:
                return source
        flat = (self.coordinator.data or {}).get("flat") or {}
        source = flat.get("current_price_source")
        return source if isinstance(source, str) and source else None

    def _published_until(self) -> str | None:
        source = (self.coordinator.price_series or {}).get("source")
        if isinstance(source, dict):
            value = source.get("published_until")
            if isinstance(value, str) and value:
                return value
        return None

    @property
    def available(self) -> bool:
        """Unknown provenance is reported as unknown, not as "no problem".

        An older API that names no source at all would otherwise read as a
        permanent all-clear, which is the one answer this entity must never
        give wrongly.
        """
        return super().available and self._current_source() is not None

    @property
    def is_on(self) -> bool:
        return self._current_source() == "forecast"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the flag rests on, so a card can say it in words.

        `published_until` is the end of the last published slot in the
        series this installation was served, taken from the API, and null
        while nothing in it is published. How long the flag has been on is
        not repeated here - Home Assistant keeps that as the entity's own
        last_changed.
        """
        return {
            "price_source": self._current_source(),
            "published_until": self._published_until(),
        }


class EnergyPriceForecastCombinedWindowBinarySensor(
    QuarterHourStateRefreshMixin, EnergyPriceForecastEntity, BinarySensorEntity
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
