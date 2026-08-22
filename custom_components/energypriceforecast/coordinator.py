"""Data coordinator for Energy Price Forecast EU."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import EnergyPriceForecastApi, EnergyPriceForecastApiError
from .const import DEFAULT_UPDATE_INTERVAL_MINUTES, NAME

_LOGGER = logging.getLogger(__name__)


def _cheapest_hour_blocks(
    entries: list[dict[str, Any]], count: int
) -> list[dict[str, Any]]:
    """Group price entries into calendar hours, then pick the count
    cheapest hours within each local calendar day separately.

    Selected hours may be non-contiguous within a day (e.g. hour 2 and 5
    of today) - unlike the API's summary endpoint, which only finds the
    single best *contiguous* window. Picking independently per calendar
    day, rather than across the whole configured horizon at once, is
    what makes a recurring automation ("run the washing machine during
    the N cheapest hours") actually recur every day: a shared N-hour
    budget across the whole horizon could otherwise land entirely on
    the cheaper of two days, leaving the other day with none at all.
    Hours that have already fully passed are excluded.
    """
    now_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    buckets: dict[datetime, list[float]] = {}
    for entry in entries:
        start_raw = entry.get("start")
        value = entry.get("value")
        if not isinstance(start_raw, str) or not isinstance(value, (int, float)):
            continue
        try:
            start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        hour_start = start.replace(minute=0, second=0, microsecond=0)
        if hour_start < now_hour:
            continue
        buckets.setdefault(hour_start, []).append(float(value))

    hours = [
        {
            "start": start,
            "end": start + timedelta(hours=1),
            "average_value": sum(values) / len(values),
        }
        for start, values in buckets.items()
    ]

    by_local_day: dict[date, list[dict[str, Any]]] = {}
    for hour in hours:
        local_day = dt_util.as_local(hour["start"]).date()
        by_local_day.setdefault(local_day, []).append(hour)

    cheapest: list[dict[str, Any]] = []
    for day_hours in by_local_day.values():
        cheapest.extend(
            sorted(day_hours, key=lambda hour: hour["average_value"])[:count]
        )
    return sorted(cheapest, key=lambda hour: hour["start"])


class EnergyPriceForecastCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Share one API request between every entity of one market."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EnergyPriceForecastApi,
        retail_pricing: bool = False,
        postal_code: str | None = None,
        update_interval_minutes: int = DEFAULT_UPDATE_INTERVAL_MINUTES,
        cheapest_hours_count: int = 0,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=NAME,
            update_interval=timedelta(minutes=update_interval_minutes),
        )
        self.api = api
        self.retail_pricing = retail_pricing
        self.postal_code = postal_code
        self.cheapest_hours_count = cheapest_hours_count
        self.retail_data: dict[str, Any] | None = None
        self.retail_summary: dict[str, Any] | None = None
        self.price_series: dict[str, Any] | None = None
        self.cheapest_hours: list[dict[str, Any]] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            summary = await self.api.async_get_summary()
        except EnergyPriceForecastApiError as err:
            raise UpdateFailed(str(err)) from err

        if self.retail_pricing:
            # Retail pricing is a supplementary feature: a temporary failure
            # (for example the API not offering it for this market right
            # now) should not take the core price/CO2 sensors down with it.
            try:
                self.retail_data = await self.api.async_get_prices(
                    price_mode="retail", postal_code=self.postal_code
                )
            except EnergyPriceForecastApiError as err:
                _LOGGER.warning("Retail price update failed: %s", err)
            try:
                self.retail_summary = await self.api.async_get_summary(
                    price_mode="retail", postal_code=self.postal_code
                )
            except EnergyPriceForecastApiError as err:
                _LOGGER.warning("Retail summary update failed: %s", err)

        # The raw price series backs both the price-series sensor (for
        # charting, e.g. with apexcharts-card) and the optional
        # cheapest-hours feature. Fetched unconditionally: it is the
        # forecast data this integration exists to expose, not a niche
        # add-on.
        try:
            self.price_series = await self.api.async_get_prices(price_mode="base")
        except EnergyPriceForecastApiError as err:
            _LOGGER.warning("Price series update failed: %s", err)

        if self.cheapest_hours_count > 0 and self.price_series:
            self.cheapest_hours = _cheapest_hour_blocks(
                self.price_series["entries"], self.cheapest_hours_count
            )

        return summary
