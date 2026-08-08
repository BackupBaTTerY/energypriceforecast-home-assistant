"""Data coordinator for Energy Price Forecast EU."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import EnergyPriceForecastApi, EnergyPriceForecastApiError
from .const import DEFAULT_UPDATE_INTERVAL_MINUTES, NAME

_LOGGER = logging.getLogger(__name__)


class EnergyPriceForecastCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Share one API request between every entity of one market."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: EnergyPriceForecastApi,
        retail_pricing: bool = False,
        postal_code: str | None = None,
        update_interval_minutes: int = DEFAULT_UPDATE_INTERVAL_MINUTES,
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
        self.retail_data: dict[str, Any] | None = None

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
                self.retail_data = await self.api.async_get_retail_prices(
                    self.postal_code
                )
            except EnergyPriceForecastApiError as err:
                _LOGGER.warning("Retail price update failed: %s", err)

        return summary
