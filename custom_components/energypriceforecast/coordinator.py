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
    window_is_settled,
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
        greenest_hours_count: int = 0,
        currency: str | None = None,
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
        self.greenest_hours_count = greenest_hours_count
        self.currency = currency
        self.retail_data: dict[str, Any] | None = None
        self.retail_summary: dict[str, Any] | None = None
        self.price_series: dict[str, Any] | None = None
        self.co2_series: dict[str, Any] | None = None
        self.cheapest_hours: list[dict[str, Any]] | None = None
        self.weekend_hours: list[dict[str, Any]] | None = None
        self.greenest_hours: list[dict[str, Any]] | None = None
        # What the whole block averages, locked together with its plan - the
        # baseline the plan's own average is compared against.
        self.cheapest_hours_window_average: float | None = None
        self.weekend_hours_window_average: float | None = None
        self.greenest_hours_window_average: float | None = None
        self._plan_store = Store[dict[str, Any]](
            hass, _PLAN_STORAGE_VERSION, f"{DOMAIN}_{entry_id}_plans"
        )
        # One entry per block, keyed by plan and block start. Values are
        # the stored plan; a bare list is the shape an older version wrote.
        self._plan_cache: dict[str, Any] | None = None

    @property
    def plan_series(self) -> dict[str, Any] | None:
        """The price series the plans are built on, and priced in.

        With retail pricing enabled that is the retail series: it is what a
        planned hour actually costs the user, and the markup is not a flat
        offset, so it can reorder the hours as well as change the saving.
        """
        return self.retail_data if self.retail_pricing else self.price_series

    async def _async_load_plan_cache(self) -> dict[str, Any]:
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
        now: datetime,
    ) -> dict[str, Any] | None:
        """Return the plan for one block, computing and locking it once.

        Every subsequent call for the same block (same plan_key and window
        start) returns the exact hours picked the first time, even if the
        forecast has since changed - see planning.py for why that matters.

        The single exception is the day-ahead auction. A block that reached
        past the published prices was planned on a forecast, and the picks
        are only as good as that guess was; the integration's own quality
        figures put the cheapest window exactly right on 17 of 30 days. So
        once day-ahead prices cover the rest of the block, the hours that
        have not started yet are picked once more from the settled prices
        and the plan is marked settled, after which nothing moves it again.
        """
        cache = await self._async_load_plan_cache()
        cache_key = f"{plan_key}|{window_start.isoformat()}"
        cached = cache.get(cache_key)
        # Only the part of the block that is still ahead can be settled: the
        # price series starts at the present, so the hours a block has
        # already run through are simply not in it any more.
        settled = window_is_settled(entries, max(window_start, now), window_end)

        if cached is not None:
            plan = self._plan_from_cache(cached)
            if self._plan_is_settled(cached) or not settled:
                return plan
            resettled = self._resettle_plan(
                plan, count, window_start, window_end, entries, now
            )
            if resettled is None:
                return plan
            plan = resettled
        else:
            plan = select_cheapest_hours(
                entries, count, window_start, window_end, available_from
            )
            if plan is None:
                return None

        await self._async_store_plan(cache, cache_key, plan, settled=settled)
        return plan

    @staticmethod
    def _plan_from_cache(cached: Any) -> dict[str, Any]:
        # Plans cached before the block average was stored are a bare list.
        # They stay valid as plans - only the baseline is missing, and
        # recomputing it now could contradict the picks it would be shown
        # next to, so it stays absent for that block.
        if isinstance(cached, list):
            cached = {"hours": cached, "window_average_value": None}
        return {
            "hours": [
                {
                    "start": datetime.fromisoformat(hour["start"]),
                    "end": datetime.fromisoformat(hour["end"]),
                    "average_value": hour["average_value"],
                }
                for hour in cached.get("hours", [])
            ],
            "window_average_value": cached.get("window_average_value"),
        }

    @staticmethod
    def _plan_is_settled(cached: Any) -> bool:
        # A plan stored by a version that did not track this is left alone.
        # It was locked under the old rule, and re-picking it now would move
        # hours a running automation is already counting on.
        if not isinstance(cached, dict):
            return True
        return bool(cached.get("settled", True))

    @staticmethod
    def _resettle_plan(
        plan: dict[str, Any],
        count: int,
        window_start: datetime,
        window_end: datetime,
        entries: list[dict[str, Any]],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Re-pick the hours of a forecast-planned block that are still ahead.

        Hours that have already started stay exactly where they are, and
        they keep their slot in the count - an automation that is running
        right now must not be pulled out from under itself.

        The block baseline is recomputed along with the picks. It was built
        from the same forecast, so leaving it in place would compare settled
        picks against a guessed average.
        """
        kept = [hour for hour in plan["hours"] if hour["start"] <= now]
        remaining = count - len(kept)
        if remaining < 1:
            # Nothing left to move: the whole plan is already under way.
            return plan
        # Start the re-pick after the last kept hour so a running hour cannot
        # be picked a second time.
        pick_from = max([now] + [hour["end"] for hour in kept])
        replan = select_cheapest_hours(
            entries, remaining, window_start, window_end, pick_from
        )
        if replan is None:
            return None
        return {
            "hours": sorted(
                kept + replan["hours"], key=lambda hour: hour["start"]
            ),
            "window_average_value": replan["window_average_value"],
        }

    async def _async_store_plan(
        self,
        cache: dict[str, Any],
        cache_key: str,
        plan: dict[str, Any],
        settled: bool,
    ) -> None:
        cache[cache_key] = {
            "hours": [
                {
                    "start": hour["start"].isoformat(),
                    "end": hour["end"].isoformat(),
                    "average_value": hour["average_value"],
                }
                for hour in plan["hours"]
            ],
            "window_average_value": plan["window_average_value"],
            "settled": settled,
        }
        if len(cache) > _MAX_CACHED_PLANS:
            for stale_key in list(cache)[: len(cache) - _MAX_CACHED_PLANS]:
                del cache[stale_key]
        await self._plan_store.async_save(cache)

    @staticmethod
    def _co2_series_from(summary: dict[str, Any]) -> dict[str, Any] | None:
        """Lift the summary's CO2 slots into the same shape as a price series.

        Everything downstream - the series sensor, the planner - already
        speaks that shape, so CO2 needs no parallel code path.
        """
        entries = (summary.get("series") or {}).get("co2")
        if not isinstance(entries, list) or not entries:
            return None
        unit = (summary.get("co2") or {}).get("unit") or "gCO2/kWh"
        return {"unit": unit, "entries": entries}

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            summary = await self.api.async_get_summary(include_series=True)
        except EnergyPriceForecastApiError as err:
            raise UpdateFailed(str(err)) from err

        # A market without CO2 coverage returns no slots. Keeping the last
        # ones would be worse than showing nothing, so this clears.
        self.co2_series = self._co2_series_from(summary)

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

        # The raw price series backs the price-series sensor (for charting,
        # e.g. with apexcharts-card), and the plans too unless retail
        # pricing is on. Fetched unconditionally: it is the forecast data
        # this integration exists to expose, not a niche add-on.
        try:
            self.price_series = await self.api.async_get_prices(price_mode="base")
        except EnergyPriceForecastApiError as err:
            _LOGGER.warning("Price series update failed: %s", err)

        now = dt_util.utcnow()

        # Plans are built on the series the user actually pays. With retail
        # pricing on, the spot price is neither what a planned hour costs nor
        # what it saves: the retail markup is not a flat offset, so it can in
        # principle reorder the hours, and it always compresses the saving -
        # against real data a 43% retail saving showed as 100% on spot.
        # Falling back to spot when a retail update failed would silently mix
        # the two, so the plan simply keeps its previous value until retail
        # data is back.
        plan_series = self.plan_series
        plan_price_mode = "retail" if self.retail_pricing else "base"
        # A stored plan keeps its averages in the currency it was priced in,
        # so the currency belongs in its key: switching from EUR to CZK must
        # start a fresh plan rather than show a euro average next to koruna
        # prices. It is only added when a currency was actually asked for,
        # which keeps every key written before this option existed exactly
        # as it was - a changed key would re-pick a plan halfway through its
        # block. The picked hours do not move either way: the API converts a
        # whole response with one rate, and that keeps their order.
        plan_basis = (
            f"{plan_price_mode}|{self.currency}" if self.currency else plan_price_mode
        )

        if self.cheapest_hours_count > 0 and plan_series:
            window_start, window_end = fixed_repeating_window(
                now, self.cheapest_hours_start_hour, self.cheapest_hours_window_hours
            )
            plan = await self._async_get_plan(
                f"block|{plan_basis}|{self.cheapest_hours_count}|"
                f"{self.cheapest_hours_window_hours}|{self.cheapest_hours_start_hour}",
                window_start,
                window_end,
                self.cheapest_hours_count,
                plan_series["entries"],
                # Past hours of the current block need no coverage - only
                # gaps from now onward would make the plan unreliable.
                max(window_start, now),
                now,
            )
            self.cheapest_hours = plan["hours"] if plan else None
            self.cheapest_hours_window_average = (
                plan["window_average_value"] if plan else None
            )

        if self.weekend_hours_count > 0 and plan_series:
            window_start, window_end = fixed_weekend_window(now)
            plan = await self._async_get_plan(
                f"weekend|{plan_basis}|{self.weekend_hours_count}",
                window_start,
                window_end,
                self.weekend_hours_count,
                plan_series["entries"],
                # The weekend plan requires the *entire* Sat-Mon window to
                # be covered before it locks in, even hours before "now" -
                # so a plan built after a late reload is never partial.
                window_start,
                now,
            )
            self.weekend_hours = plan["hours"] if plan else None
            self.weekend_hours_window_average = (
                plan["window_average_value"] if plan else None
            )

        # The greenest hours share the block geometry with the cheapest ones -
        # same length, same anchor - but are picked on CO2 intensity. The
        # planner ranks whatever "value" it is handed, so this is the same
        # locking, the same coverage rule and the same one-off correction when
        # the numbers settle, applied to a different series.
        if self.greenest_hours_count > 0 and self.co2_series:
            window_start, window_end = fixed_repeating_window(
                now, self.cheapest_hours_start_hour, self.cheapest_hours_window_hours
            )
            plan = await self._async_get_plan(
                f"greenest|co2|{self.greenest_hours_count}|"
                f"{self.cheapest_hours_window_hours}|{self.cheapest_hours_start_hour}",
                window_start,
                window_end,
                self.greenest_hours_count,
                self.co2_series["entries"],
                max(window_start, now),
                now,
            )
            self.greenest_hours = plan["hours"] if plan else None
            self.greenest_hours_window_average = (
                plan["window_average_value"] if plan else None
            )

        return summary
