"""Client for the Energy Price Forecast EU API."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import LOCAL_CURRENCY_BY_MARKET, VERSION

_LOGGER = logging.getLogger(__name__)


class EnergyPriceForecastApiError(Exception):
    """Base API error."""


class EnergyPriceForecastAuthError(EnergyPriceForecastApiError):
    """Authentication or authorization failed."""


class EnergyPriceForecastConnectionError(EnergyPriceForecastApiError):
    """The API could not be reached."""


class EnergyPriceForecastInvalidResponse(EnergyPriceForecastApiError):
    """The API response did not match the expected contract."""


class EnergyPriceForecastRetailUnavailable(EnergyPriceForecastApiError):
    """Retail pricing could not be verified for this market/postal code."""


# The public API never returns HTTP 401/403 for a rejected key: it responds
# with 200 and reports the outcome in meta.api_key_state instead. Only these
# states mean the key itself was examined and refused.
REJECTED_API_KEY_STATES = frozenset(
    {
        "invalid",
        "invalid_format",
        "revoked",
        "inactive",
        "expired",
    }
)

# These say nothing about whether the key is valid. The API sets
# "lookup_failed" when its own key lookup raised and it fell back to serving
# the request with public access, and "rate_limited" accompanies an HTTP 429
# for a valid key that hit its daily quota (so it never reaches the 200 path
# here at all). Both are transient and still return usable data, so treating
# them as a rejected key would take every entity down - reporting
# "unavailable" for a momentary backend hiccup - instead of quietly serving
# the public-horizon data the response actually contains.
DEGRADED_API_KEY_STATES = frozenset({"lookup_failed", "rate_limited"})


def requested_currency(market: str, local_currency: bool) -> str | None:
    """The currency to ask the API for, or None to leave its default alone.

    None whenever the option is off or the market has nothing to switch to:
    euro markets are already in their own currency, and Denmark and Norway
    are answered in DKK and NOK without being asked.
    """
    if not local_currency:
        return None
    return LOCAL_CURRENCY_BY_MARKET.get(str(market).upper())


class EnergyPriceForecastApi:
    """Small asynchronous API client using Home Assistant's shared session."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        market: str,
        horizon_hours: int,
        window_hours: int,
        api_key: str | None = None,
        prices_url: str | None = None,
        currency: str | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url
        self._prices_url = prices_url
        self._market = market
        self._horizon_hours = horizon_hours
        self._window_hours = window_hours
        self._api_key = (api_key or "").strip()
        self._currency = (currency or "").strip().upper() or None

    def _with_currency(self, params: dict[str, str]) -> dict[str, str]:
        """Ask for the configured currency, or leave the API's default alone.

        Without the parameter the API answers in the market's own default -
        euro for most markets, DKK and NOK for Denmark and Norway - which is
        exactly what every entry configured before this option existed got,
        so leaving it out is what keeps those requests unchanged.
        """
        if self._currency:
            params["currency"] = self._currency
        return params

    async def _async_request(
        self, url: str, params: dict[str, str]
    ) -> dict[str, Any]:
        """Call one endpoint and return its parsed JSON body."""
        headers = {
            "Accept": "application/json",
            "User-Agent": f"EnergyPriceForecast-HomeAssistant/{VERSION}",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=30,
            ) as response:
                if response.status in (401, 403):
                    raise EnergyPriceForecastAuthError(
                        "The API key was rejected or is not authorized."
                    )
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except EnergyPriceForecastAuthError:
            raise
        except (ClientError, ClientResponseError, TimeoutError) as err:
            raise EnergyPriceForecastConnectionError(str(err)) from err
        except ValueError as err:
            raise EnergyPriceForecastInvalidResponse(
                "The API did not return valid JSON."
            ) from err

        if not isinstance(payload, dict):
            raise EnergyPriceForecastInvalidResponse("The response is not an object.")
        return payload

    async def async_get_summary(
        self,
        price_mode: str = "base",
        postal_code: str | None = None,
        include_series: bool = False,
    ) -> dict[str, Any]:
        """Fetch and validate one automation summary.

        include_series asks for the raw price and CO2 series. The CO2 series
        has no endpoint of its own - the summary is the only place it is
        published - and one flag returns both, so asking for it also repeats
        the price series the prices endpoint already serves. Callers that do
        not need CO2 slots should leave it off rather than pay for that.
        """
        params = {
            "country": self._market.lower(),
            "hours": str(self._horizon_hours),
            "summary_hours": str(self._horizon_hours),
            "window_hours": str(self._window_hours),
            "include_series": "true" if include_series else "false",
            "prefer_live_day_ahead": "true",
            "price_mode": price_mode,
        }
        if postal_code:
            params["plz"] = postal_code
        payload = await self._async_request(
            self._base_url, self._with_currency(params)
        )

        if payload.get("format") != "home-assistant-summary":
            raise EnergyPriceForecastInvalidResponse("Unexpected response format.")
        if str(payload.get("country", "")).upper() != self._market.upper():
            raise EnergyPriceForecastInvalidResponse("Unexpected market in response.")
        if not isinstance(payload.get("flat"), dict):
            raise EnergyPriceForecastInvalidResponse("The flat summary is missing.")
        if not isinstance(payload.get("meta"), dict):
            raise EnergyPriceForecastInvalidResponse("The access metadata is missing.")
        self._raise_if_key_rejected(payload["meta"].get("api_key_state"))
        return payload

    async def async_get_prices(
        self, price_mode: str = "base", postal_code: str | None = None
    ) -> dict[str, Any]:
        """Fetch and validate a raw price series (base market or retail)."""
        if not self._prices_url:
            raise EnergyPriceForecastInvalidResponse("No prices endpoint configured.")

        params = {
            "country": self._market.lower(),
            "hours": str(self._horizon_hours),
            "price_mode": price_mode,
        }
        if postal_code:
            params["plz"] = postal_code
        payload = await self._async_request(
            self._prices_url, self._with_currency(params)
        )

        if payload.get("format") != "home-assistant-prices":
            raise EnergyPriceForecastInvalidResponse("Unexpected response format.")
        if str(payload.get("country", "")).upper() != self._market.upper():
            raise EnergyPriceForecastInvalidResponse("Unexpected market in response.")
        if not isinstance(payload.get("entries"), list):
            raise EnergyPriceForecastInvalidResponse("The price entries are missing.")
        return payload

    def _raise_if_key_rejected(self, api_key_state: Any) -> None:
        if not self._api_key:
            return
        if api_key_state in REJECTED_API_KEY_STATES:
            raise EnergyPriceForecastAuthError(
                f"The API key was not accepted (state: {api_key_state})."
            )
        if api_key_state in DEGRADED_API_KEY_STATES:
            _LOGGER.warning(
                "The API could not apply the configured API key (state: %s). "
                "Serving this update with public access limits, so the "
                "horizon may be shorter than requested",
                api_key_state,
            )
