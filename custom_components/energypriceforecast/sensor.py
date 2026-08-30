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
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import EnergyPriceForecastCoordinator
from .entity import EnergyPriceForecastEntity
from .planning import duration_weighted_mean


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
    """Split published day-ahead entries into today's and tomorrow's, locally.

    Matches the raw_today/raw_tomorrow attribute convention used by the
    Nordpool integration, so existing apexcharts-card templates work
    with minimal changes. Only contains the hours actually returned by
    the API - a rolling window starting at "now" - not the full
    calendar day; hours of today that have already passed are not
    included since the API does not look backward from local midnight.

    Forecast entries are left out on purpose, which also follows that
    convention: tomorrow stays empty until its day-ahead prices are
    published, rather than being filled with estimates. Including them
    would present modelled values as known prices, and would also make
    them appear twice on a chart - once here and once in raw_forecast,
    the second time starting before this series ends.
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
        # Anything not explicitly forecast counts as known: an entry
        # without a source is treated the way it always has been.
        if entry.get("source") == "forecast":
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


def _forecast_only(entries: Any) -> list[dict[str, Any]]:
    """Return only the entries beyond the published day-ahead window.

    The API marks each entry with source "day_ahead" or "forecast".
    raw_today/raw_tomorrow only ever cover the published day-ahead
    window (like Nordpool's convention), so they can never show the
    ML/weather-based forecast that starts once day-ahead coverage
    ends - typically from the day after tomorrow, with a longer
    horizon config or API key. This attribute exists to make that
    forecast portion chartable on its own.
    """
    if not isinstance(entries, list):
        return []
    return [
        {"start": entry.get("start"), "end": entry.get("end"), "value": entry.get("value")}
        for entry in entries
        if entry.get("source") == "forecast"
    ]


# The raw series attributes hold one entry per 15-minute slot across the
# whole horizon - at 120 hours that is far past the recorder's 16 KB per-state
# attribute limit, which makes it drop the attributes and log a warning on
# every update. They are meant to be read live (charts, templates), never
# from history, so keep them out of the database entirely.
_SERIES_ATTRIBUTES = frozenset({"raw_today", "raw_tomorrow", "raw_forecast"})


class _StickyUnitMixin:
    """Keep the last known unit when an update leaves the payload empty.

    Units are read out of the API payload, so a failed or partial update
    would otherwise flip them to None. Home Assistant reads that as a unit
    change and then permanently suppresses long-term statistics for the
    entity ("cannot be converted to the unit of previously compiled
    statistics") - a lasting consequence for a momentary outage.
    """

    _last_unit: str | None = None

    def _sticky_unit(self, unit: Any) -> str | None:
        if isinstance(unit, str) and unit:
            self._last_unit = unit
        return self._last_unit


def _day_statistics(
    today: list[dict[str, Any]], current: float | None
) -> dict[str, Any]:
    """Nordpool-style average/min/max over today's published prices.

    Named to match the Nordpool integration so its templates port over, but
    the scope genuinely differs and callers need to know it: the API does
    not look back past "now", so this covers today's *remaining* published
    hours, not the calendar day. Late in the evening that is a handful of
    hours, and once tomorrow's prices are out it says nothing about them.

    price_percent_to_average follows from that same window, so it answers
    "how does this hour compare with the rest of today" - which is the
    question worth asking anyway, since the past is not actionable.
    """
    values = [
        entry["value"]
        for entry in today
        if isinstance(entry.get("value"), (int, float))
    ]
    if not values:
        return {
            "average": None,
            "min": None,
            "max": None,
            "price_percent_to_average": None,
        }
    average = sum(values) / len(values)
    percent = None
    # Same reasoning as the plan saving sensor: a percentage of a zero or
    # negative baseline conveys nothing, and negative prices are routine.
    if current is not None and average > 0:
        percent = current / average * 100
    return {
        "average": round(average, 6),
        "min": min(values),
        "max": max(values),
        "price_percent_to_average": None if percent is None else round(percent, 1),
    }


def _next_planned_start(hours: list[dict[str, Any]] | None) -> datetime | None:
    """Start of the first planned hour that has not begun yet.

    A plan is a fixed list covering its whole block, so the first entry stays
    the first entry all block long. Reporting it as the "next" hour leaves the
    sensor pointing further and further into the past once that hour is over,
    which is both wrong and useless to an automation asking when the next
    cheap hour begins. Returns None once every planned hour has started - the
    plan for this block is then simply done.
    """
    if not hours:
        return None
    now = datetime.now(timezone.utc)
    # Plans are stored sorted by start, so the first future entry is the next.
    return next((hour["start"] for hour in hours if hour["start"] > now), None)


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
        key="cheapest_window_remaining",
        translation_key="cheapest_window_remaining",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda data: _path(
            data, "flat", "cheapest_window_remaining_minutes"
        ),
    ),
    EnergyPriceForecastSensorDescription(
        key="greenest_window_remaining",
        translation_key="greenest_window_remaining",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda data: _path(
            data, "flat", "greenest_window_remaining_minutes"
        ),
    ),
    EnergyPriceForecastSensorDescription(
        key="price_source",
        translation_key="price_source",
        icon="mdi:database-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _path(data, "flat", "current_price_source"),
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


RETAIL_WINDOW_SENSORS: tuple[EnergyPriceForecastSensorDescription, ...] = (
    EnergyPriceForecastSensorDescription(
        key="retail_cheapest_window_average_price",
        translation_key="retail_cheapest_window_average_price",
        icon="mdi:cash-clock",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda data: _path(data, "flat", "best_price_window_avg_price"),
        unit_fn=lambda data: _path(data, "flat", "current_price_unit"),
    ),
    EnergyPriceForecastSensorDescription(
        key="retail_cheapest_window_start",
        translation_key="retail_cheapest_window_start",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _timestamp(
            _path(data, "flat", "best_price_window_start")
        ),
    ),
    EnergyPriceForecastSensorDescription(
        key="retail_cheapest_window_end",
        translation_key="retail_cheapest_window_end",
        icon="mdi:clock-end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _timestamp(_path(data, "flat", "best_price_window_end")),
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
        entities.extend(
            EnergyPriceForecastRetailWindowSensor(coordinator, entry, description)
            for description in RETAIL_WINDOW_SENSORS
        )
    if coordinator.cheapest_hours_count > 0:
        entities.append(EnergyPriceForecastCheapestHoursSensor(coordinator, entry))
        entities.append(EnergyPriceForecastPlanAveragePriceSensor(coordinator, entry))
        entities.append(EnergyPriceForecastPlanSavingSensor(coordinator, entry))
    if coordinator.weekend_hours_count > 0:
        entities.append(EnergyPriceForecastWeekendHoursSensor(coordinator, entry))
        entities.append(
            EnergyPriceForecastWeekendPlanAveragePriceSensor(coordinator, entry)
        )
        entities.append(EnergyPriceForecastWeekendPlanSavingSensor(coordinator, entry))
    async_add_entities(entities)


class EnergyPriceForecastSensor(
    _StickyUnitMixin, EnergyPriceForecastEntity, SensorEntity
):
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
            return self._sticky_unit(self.entity_description.unit_fn(self.coordinator.data))
        return self.entity_description.native_unit_of_measurement


class EnergyPriceForecastRetailWindowSensor(
    _StickyUnitMixin, EnergyPriceForecastEntity, SensorEntity
):
    """One cheapest-window value read from the retail-mode summary.

    Only created when retail pricing was enabled. The base window
    sensors (EnergyPriceForecastSensor with the SENSORS descriptions)
    always reflect spot prices, because coordinator.data is always a
    base-mode summary - this mirrors those, but backed by
    coordinator.retail_summary, so "cheapest window" reflects what the
    window actually costs on the configured retail tariff, not the
    underlying spot price.
    """

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
    def available(self) -> bool:
        return super().available and self.coordinator.retail_summary is not None

    @property
    def native_value(self) -> Any:
        if self.coordinator.retail_summary is None:
            return None
        return self.entity_description.value_fn(self.coordinator.retail_summary)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.coordinator.retail_summary is None:
            return self._sticky_unit(None)
        if self.entity_description.unit_fn is not None:
            return self._sticky_unit(
                self.entity_description.unit_fn(self.coordinator.retail_summary)
            )
        return self.entity_description.native_unit_of_measurement


class EnergyPriceForecastRetailPriceSensor(
    _StickyUnitMixin, EnergyPriceForecastEntity, SensorEntity
):
    """Current assumption-based retail (all-in) electricity price.

    Only created when retail pricing was enabled during setup. Backed by
    coordinator.retail_data rather than the shared summary response.
    State mirrors the current retail price; raw_today/raw_tomorrow
    attributes carry the full retail series, same shape as
    EnergyPriceForecastPriceSeriesSensor but with retail values.
    raw_forecast carries the entries beyond published day-ahead
    coverage, i.e. the actual ML/weather-based forecast.
    """

    _attr_translation_key = "retail_current_price"
    _attr_icon = "mdi:cash-multiple"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _unrecorded_attributes = _SERIES_ATTRIBUTES

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
        return self._sticky_unit(_path(self.coordinator.retail_data or {}, "unit"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entries = _path(self.coordinator.retail_data or {}, "entries")
        today, tomorrow = _split_today_tomorrow(entries)
        return {
            "raw_today": today,
            "raw_tomorrow": tomorrow,
            "raw_forecast": _forecast_only(entries),
            **_day_statistics(today, self.native_value),
        }


class EnergyPriceForecastPriceSeriesSensor(
    _StickyUnitMixin, EnergyPriceForecastEntity, SensorEntity
):
    """Raw price forecast series for charting and custom automations.

    Always created (unlike the other optional sensors): forecasting the
    price series is this integration's core purpose, not a niche
    add-on. State mirrors the current market price; raw_today/
    raw_tomorrow attributes carry the full series, and raw_forecast
    carries the entries beyond published day-ahead coverage - the
    actual ML/weather-based forecast, richer with a longer configured
    horizon. Backed by coordinator.price_series rather than the shared
    summary response.
    """

    _attr_translation_key = "price_series"
    _attr_icon = "mdi:chart-line"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4
    _unrecorded_attributes = _SERIES_ATTRIBUTES

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
        return self._sticky_unit(_path(self.coordinator.price_series or {}, "unit"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        entries = _path(self.coordinator.price_series or {}, "entries")
        today, tomorrow = _split_today_tomorrow(entries)
        return {
            "raw_today": today,
            "raw_tomorrow": tomorrow,
            "raw_forecast": _forecast_only(entries),
            **_day_statistics(today, self.native_value),
        }


class EnergyPriceForecastCheapestHoursSensor(EnergyPriceForecastEntity, SensorEntity):
    """Start of the next of the N cheapest upcoming hours.

    The hours may be non-contiguous, unlike the API's single best
    continuous window. Only created when a positive hour count was
    configured. Backed by coordinator.cheapest_hours.
    """

    _attr_translation_key = "cheapest_hours_next_start"
    _attr_icon = "mdi:sort-clock-ascending"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"hours"})

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "cheapest_hours_next_start")

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.cheapest_hours)

    @property
    def native_value(self) -> Any:
        return _next_planned_start(self.coordinator.cheapest_hours)

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


class _PlanStatisticSensor(_StickyUnitMixin, EnergyPriceForecastEntity, SensorEntity):
    """Base for the numbers that say what a locked plan is worth.

    Both values come from the plan itself, so they are as fixed as the plan
    is: they are computed once when the block's hours are picked and do not
    move afterwards. A figure that drifted while the plan stayed put would
    be worse than none at all.
    """

    _hours_attribute = "cheapest_hours"
    _window_average_attribute = "cheapest_hours_window_average"

    @property
    def _hours(self) -> list[dict[str, Any]] | None:
        return getattr(self.coordinator, self._hours_attribute)

    @property
    def _window_average(self) -> float | None:
        return getattr(self.coordinator, self._window_average_attribute)

    @property
    def _plan_average(self) -> float | None:
        return duration_weighted_mean(self._hours or [])

    @property
    def available(self) -> bool:
        return super().available and bool(self._hours)


class EnergyPriceForecastPlanAveragePriceSensor(_PlanStatisticSensor):
    """What the hours this plan picked cost on average."""

    _attr_translation_key = "cheapest_hours_average_price"
    _attr_icon = "mdi:cash-check"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 4

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, self._attr_translation_key)

    @property
    def native_value(self) -> Any:
        return self._plan_average

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._sticky_unit(_path(self.coordinator.price_series or {}, "unit"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"window_average_value": self._window_average}


class EnergyPriceForecastPlanSavingSensor(_PlanStatisticSensor):
    """How far below the block's own average the plan lands, in percent.

    The comparison is against the whole block, not against some notional
    tariff: it answers "what did picking these hours gain over running at an
    arbitrary time in the same period", which is the only saving this
    integration can state without knowing anything about consumption.
    """

    _attr_translation_key = "cheapest_hours_saving"
    _attr_icon = "mdi:piggy-bank"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 1

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, self._attr_translation_key)

    @property
    def native_value(self) -> Any:
        window_average = self._window_average
        plan_average = self._plan_average
        if window_average is None or plan_average is None:
            return None
        # Percentages of a zero or negative baseline are not wrong so much
        # as meaningless - "40% cheaper than -0.001 EUR/kWh" tells nobody
        # anything. Negative prices are normal here, so this is not a
        # theoretical case.
        if window_average <= 0:
            return None
        return (window_average - plan_average) / window_average * 100

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "plan_average_value": self._plan_average,
            "window_average_value": self._window_average,
        }


class EnergyPriceForecastWeekendPlanAveragePriceSensor(
    EnergyPriceForecastPlanAveragePriceSensor
):
    """Average price of the weekend plan's picked hours."""

    _attr_translation_key = "weekend_hours_average_price"
    _hours_attribute = "weekend_hours"
    _window_average_attribute = "weekend_hours_window_average"


class EnergyPriceForecastWeekendPlanSavingSensor(EnergyPriceForecastPlanSavingSensor):
    """How far below the weekend block's average its plan lands."""

    _attr_translation_key = "weekend_hours_saving"
    _hours_attribute = "weekend_hours"
    _window_average_attribute = "weekend_hours_window_average"


class EnergyPriceForecastWeekendHoursSensor(EnergyPriceForecastEntity, SensorEntity):
    """Start of the next of the N cheapest hours in this weekend's plan.

    A separate, independently-locked plan for the fixed Saturday 00:00 -
    Monday 00:00 block (see planning.fixed_weekend_window), for loads
    that are specifically flexible on weekends, e.g. EV charging. Only
    created when a positive weekend hour count was configured. Backed by
    coordinator.weekend_hours.
    """

    _attr_translation_key = "weekend_hours_next_start"
    _attr_icon = "mdi:calendar-weekend"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"hours"})

    def __init__(
        self, coordinator: EnergyPriceForecastCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "weekend_hours_next_start")

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.weekend_hours)

    @property
    def native_value(self) -> Any:
        return _next_planned_start(self.coordinator.weekend_hours)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        hours = self.coordinator.weekend_hours or []
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
