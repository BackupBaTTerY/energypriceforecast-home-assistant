"""Client for the Energy Price Forecast EU API."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession


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
# with 200 and reports the outcome in meta.api_key_state instead. States other
# than "valid" mean the supplied key was not accepted.
REJECTED_API_KEY_STATES = frozenset(
    {
        "invalid",
        "invalid_format",
        "revoked",
        "inactive",
        "expired",
        "lookup_failed",
        "rate_limited",
    }
)


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
    ) -> None:
        self._session = session
        self._base_url = base_url
        self._prices_url = prices_url
        self._market = market
        self._horizon_hours = horizon_hours
        self._window_hours = window_hours
        self._api_key = (api_key or "").strip()

    async def _async_request(
        self, url: str, params: dict[str, str]
    ) -> dict[str, Any]:
        """Call one endpoint and return its parsed JSON body."""
        headers = {
            "Accept": "application/json",
            "User-Agent": "EnergyPriceForecast-HomeAssistant/0.1.0",
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
        self, price_mode: str = "base", postal_code: str | None = None
    ) -> dict[str, Any]:
        """Fetch and validate one automation summary."""
        params = {
            "country": self._market.lower(),
            "hours": str(self._horizon_hours),
            "summary_hours": str(self._horizon_hours),
            "window_hours": str(self._window_hours),
            "include_series": "false",
            "prefer_live_day_ahead": "true",
            "price_mode": price_mode,
        }
        if postal_code:
            params["plz"] = postal_code
        payload = await self._async_request(self._base_url, params)

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
        payload = await self._async_request(self._prices_url, params)

        if payload.get("format") != "home-assistant-prices":
            raise EnergyPriceForecastInvalidResponse("Unexpected response format.")
        if str(payload.get("country", "")).upper() != self._market.upper():
            raise EnergyPriceForecastInvalidResponse("Unexpected market in response.")
        if not isinstance(payload.get("entries"), list):
            raise EnergyPriceForecastInvalidResponse("The price entries are missing.")
        return payload

    def _raise_if_key_rejected(self, api_key_state: Any) -> None:
        if self._api_key and api_key_state in REJECTED_API_KEY_STATES:
            raise EnergyPriceForecastAuthError(
                f"The API key was not accepted (state: {api_key_state})."
            )
