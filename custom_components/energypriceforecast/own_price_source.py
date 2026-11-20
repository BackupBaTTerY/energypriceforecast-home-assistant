"""Read the user's own price source out of Home Assistant (1.21.0).

The pure part - formats, units, mixing - is in own_prices.py. This module
only gets the numbers out of Home Assistant:

* Nord Pool from HACS and ENTSO-e keep the prices of today and tomorrow in
  the attributes of a sensor, read straight from its state.
* Nord Pool in Home Assistant's core keeps them behind a service,
  nordpool.get_prices_for_date. Its sensors are recognised by their platform
  and by having no such attributes; the service is asked for today and
  tomorrow, in the currency of our series, and a day is not asked again
  once it has arrived.

Nothing read here is sent anywhere. It only ever goes into the series of
this integration on this Home Assistant.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .own_prices import (
    FORMAT_NORDPOOL_CORE,
    OwnSlot,
    convert,
    parse_unit,
    series_currency,
    slots_from_attributes,
    slots_from_service_response,
)

_LOGGER = logging.getLogger(__name__)

NORDPOOL_DOMAIN = "nordpool"
# A day the core service had no prices for yet is asked again after this
# long, so the hour around the auction is not spent in a tight loop.
_RETRY_AFTER = timedelta(minutes=15)

# What the diagnostics and the settings form can say about the source.
STATUS_OFF = "off"
STATUS_OK = "ok"
STATUS_ENTITY_MISSING = "entity_missing"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNKNOWN_FORMAT = "unknown_format"
STATUS_UNIT_UNKNOWN = "unit_unknown"
STATUS_CURRENCY_MISMATCH = "currency_mismatch"
STATUS_NO_PRICES = "no_prices"
STATUS_SERVICE_FAILED = "service_failed"
STATUS_SERIES_UNKNOWN = "series_unknown"


class OwnPriceSource:
    """One sensor of another integration, read into the series unit."""

    def __init__(self, hass: HomeAssistant, entity_id: str, zone: tzinfo) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.zone = zone
        self.status = STATUS_OFF
        self.format: str | None = None
        self.unit: str | None = None
        self.currency: str | None = None
        self.slots: list[OwnSlot] = []
        # The core service: per delivery day the slots, in its own unit.
        self._core_days: dict[date, list[tuple[datetime, datetime, float]]] = {}
        self._core_currency: str | None = None
        self._core_tried: dict[date, datetime] = {}

    def snapshot(self) -> dict[str, Any]:
        """For the diagnostics: what is read, never the prices themselves."""
        report: dict[str, Any] = {
            "entity_id": self.entity_id,
            "status": self.status,
            "format": self.format,
            "unit": self.unit,
            "slots": len(self.slots),
        }
        if self.slots:
            report["first_start"] = self.slots[0].start.isoformat()
            report["last_end"] = self.slots[-1].end.isoformat()
        return report

    def _fail(self, status: str) -> list[OwnSlot]:
        self.status = status
        self.slots = []
        return []

    def _is_core_nordpool(self, attributes: dict[str, Any]) -> bool:
        if "raw_today" in attributes:
            return False
        entry = er.async_get(self.hass).async_get(self.entity_id)
        return entry is not None and entry.platform == NORDPOOL_DOMAIN

    async def async_read(self, series_unit: Any, now: datetime) -> list[OwnSlot]:
        """Read the source again; the slots are in the unit of series_unit.

        On any problem the slots are empty and the status says why: the
        forecast then simply stays as it is.
        """
        wanted = series_currency(series_unit)
        if wanted is None:
            return self._fail(STATUS_SERIES_UNKNOWN)
        state = self.hass.states.get(self.entity_id)
        if state is None:
            return self._fail(STATUS_ENTITY_MISSING)
        attributes = dict(state.attributes)

        if self._is_core_nordpool(attributes):
            if not self.hass.services.has_service(
                NORDPOOL_DOMAIN, "get_prices_for_date"
            ):
                # Nord Pool from HACS shares the domain; while it is down its
                # sensor has no attributes and looks like the built-in one.
                return self._fail(STATUS_UNAVAILABLE)
            self.format = FORMAT_NORDPOOL_CORE
            self.unit = f"{wanted}/MWh"
            raw = await self._async_core_slots(wanted, now)
            if raw is None:
                return self._fail(STATUS_SERVICE_FAILED)
            factor = 0.001
        else:
            if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN) and not attributes:
                return self._fail(STATUS_UNAVAILABLE)
            found, raw = slots_from_attributes(attributes)
            self.format = found
            if found is None:
                return self._fail(STATUS_UNKNOWN_FORMAT)
            self.unit = attributes.get("unit_of_measurement")
            parsed = parse_unit(self.unit, wanted)
            if parsed is None:
                return self._fail(STATUS_UNIT_UNKNOWN)
            currency, factor = parsed
            if currency != wanted:
                self.currency = currency
                return self._fail(STATUS_CURRENCY_MISMATCH)

        self.currency = wanted
        if not raw:
            return self._fail(STATUS_NO_PRICES)
        self.slots = convert(raw, factor)
        self.status = STATUS_OK
        return self.slots

    def _core_area(self) -> tuple[str, str] | None:
        """The config entry and the delivery area behind a core sensor."""
        entry = er.async_get(self.hass).async_get(self.entity_id)
        if entry is None or entry.config_entry_id is None:
            return None
        if entry.device_id:
            device = dr.async_get(self.hass).async_get(entry.device_id)
            for domain, identifier in (device.identifiers if device else set()):
                if domain == NORDPOOL_DOMAIN:
                    return entry.config_entry_id, str(identifier).upper()
        # Without a device the unique ID still starts with the area.
        area = str(entry.unique_id or "").split("-")[0].upper()
        return (entry.config_entry_id, area) if area else None

    async def _async_core_slots(
        self, currency: str, now: datetime
    ) -> list[tuple[datetime, datetime, float]] | None:
        """Today and tomorrow from the service, each day asked until it came."""
        target = self._core_area()
        if target is None:
            return None
        config_entry, area = target
        today = now.astimezone(self.zone).date()
        days = (today, today + timedelta(days=1))
        if currency != self._core_currency:
            self._core_days.clear()
            self._core_tried.clear()
            self._core_currency = currency
        for stale in [day for day in self._core_days if day not in days]:
            del self._core_days[stale]
        failed = False
        for day in days:
            if self._core_days.get(day):
                continue
            tried = self._core_tried.get(day)
            if tried is not None and now - tried < _RETRY_AFTER:
                continue
            self._core_tried[day] = now
            try:
                response = await self.hass.services.async_call(
                    NORDPOOL_DOMAIN,
                    "get_prices_for_date",
                    {
                        "config_entry": config_entry,
                        "date": day.isoformat(),
                        "areas": [area],
                        "currency": currency,
                    },
                    blocking=True,
                    return_response=True,
                )
            except Exception as err:  # noqa: BLE001 - any failure leaves the forecast
                _LOGGER.debug("Nord Pool prices for %s not read: %s", day, err)
                failed = day == today
                continue
            self._core_days[day] = slots_from_service_response(response, area)
        if failed and not self._core_days.get(today):
            return None
        return [slot for day in days for slot in self._core_days.get(day, [])]
