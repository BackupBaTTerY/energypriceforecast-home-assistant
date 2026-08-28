"""Data coordinator for Energy Price Forecast EU."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import EnergyPriceForecastApi, EnergyPriceForecastApiError
from .const import DEFAULT_UPDATE_INTERVAL_MINUTES, DOMAIN, NAME
from .planning import (
    fixed_repeating_window,
    fixed_weekend_window,
    select_cheapest_hours,
)

_LOGGER = logging.getLogger(__name__)

_PLAN_STORAGE_VERSION = 1
_MAX_CACHED_PLANS = 50


class EnergyPriceForecastCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Share one API request between every entity of one market."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EnergyPriceForecastApi,
        entry_id: str,
        retail_pricing: bool = False,
        postal_code: str | None = None,
        update_interval_minutes: int = DEFAULT_UPDATE_INTERVAL_MINUTES,
        cheapest_hours_count: int = 0,
        cheapest_hours_window_hours: int = 24,
        cheapest_hours_start_hour: int = 0,
        weekend_hours_count: int = 0,
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
        self.cheapest_hours_window_hours = cheapest_hours_window_hours
        self.cheapest_hours_start_hour = cheapest_hours_start_hour
        self.weekend_hours_count = weekend_hours_count
        self.retail_data: dict[str, Any] | None = None
        self.retail_summary: dict[str, Any] | None = None
        self.price_series: dict[str, Any] | None = None
        self.cheapest_hours: list[dict[str, Any]] | None = None
        self.weekend_hours: list[dict[str, Any]] | None = None
        self._plan_store = Store[dict[str, Any]](
            hass, _PLAN_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_plans"
        )
        self._plan_cache: dict[str, list[dict[str, Any]]] | None = None

    async def _async_load_plan_cache(self) -> dict[str, list[dict[str, Any]]]:
        if self._plan_cache is None:
            stored = await self._plan_store.async_load()
            self._plan_cache = stored if isinstance(stored, dict) else {}
        return self._plan_cache

    async def _async_get_plan(
        self,
        plan_key: str,
        window_start: datetime,
        window_end: datetime,
        count: int,
        entries: list[dict[str, Any]],
        available_from: datetime,
    ) -> list[dict[str, Any]] | None:
        """Return the plan for one block, computing and locking it once.

        Every subsequent call for the same block (same plan_key and window
        start) returns the exact hours picked the first time, even if the
        forecast has since changed - see planning.py for why that matters.
        """
        cache = await self._async_load_plan_cache()
        cache_key = f"{plan_key}|{window_start.isoformat()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return [
                {
                    "start": datetime.fromisoformat(hour["start"]),
                    "end": datetime.fromisoformat(hour["end"]),
                    "average_value": hour["average_value"],
                }
                for hour in cached
            ]

        plan = select_cheapest_hours(
            entries, count, window_start, window_end, available_from
        )
        if plan is None:
            return None

        cache[cache_key] = [
            {
                "start": hour["start"].isoformat(),
                "end": hour["end"].isoformat(),
                "average_value": hour["average_value"],
            }
            for hour in plan
        ]
        if len(cache) > _MAX_CACHED_PLANS:
            for stale_key in list(cache)[: len(cache) - _MAX_CACHED_PLANS]:
                del cache[stale_key]
        await self._plan_store.async_save(cache)
        return plan

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
        # charting, e.g. with apexcharts-card) and the cheapest-hours
        # plans. Fetched unconditionally: it is the forecast data this
        # integration exists to expose, not a niche add-on.
        try:
            self.price_series = await self.api.async_get_prices(price_mode="base")
        except EnergyPriceForecastApiError as err:
            _LOGGER.warning("Price series update failed: %s", err)

        now = dt_util.utcnow()

        if self.cheapest_hours_count > 0 and self.price_series:
            window_start, window_end = fixed_repeating_window(
                now, self.cheapest_hours_start_hour, self.cheapest_hours_window_hours
            )
            self.cheapest_hours = await self._async_get_plan(
                f"block|{self.cheapest_hours_count}|"
                f"{self.cheapest_hours_window_hours}|{self.cheapest_hours_start_hour}",
                window_start,
                window_end,
                self.cheapest_hours_count,
                self.price_series["entries"],
                # Past hours of the current block need no coverage - only
                # gaps from now onward would make the plan unreliable.
                max(window_start, now),
            )

        if self.weekend_hours_count > 0 and self.price_series:
            window_start, window_end = fixed_weekend_window(now)
            self.weekend_hours = await self._async_get_plan(
                f"weekend|{self.weekend_hours_count}",
                window_start,
                window_end,
                self.weekend_hours_count,
                self.price_series["entries"],
                # The weekend plan requires the *entire* Sat-Mon window to
                # be covered before it locks in, even hours before "now" -
                # so a plan built after a late reload is never partial.
                window_start,
            )

        return summary
