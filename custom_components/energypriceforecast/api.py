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
    ) -> None:
        self._session = session
        self._base_url = base_url
        self._market = market
        self._horizon_hours = horizon_hours
        self._window_hours = window_hours
        self._api_key = (api_key or "").strip()

    async def async_get_summary(self) -> dict[str, Any]:
        """Fetch and validate one automation summary."""
        headers = {
            "Accept": "application/json",
            "User-Agent": "EnergyPriceForecast-HomeAssistant/0.1.0",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        params = {
            "country": self._market.lower(),
            "hours": str(self._horizon_hours),
            "summary_hours": str(self._horizon_hours),
            "window_hours": str(self._window_hours),
            "include_series": "false",
            "prefer_live_day_ahead": "true",
        }

        try:
            async with self._session.get(
                self._base_url,
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
        if payload.get("format") != "home-assistant-summary":
            raise EnergyPriceForecastInvalidResponse("Unexpected response format.")
        if str(payload.get("country", "")).upper() != self._market.upper():
            raise EnergyPriceForecastInvalidResponse("Unexpected market in response.")
        if not isinstance(payload.get("flat"), dict):
            raise EnergyPriceForecastInvalidResponse("The flat summary is missing.")
        if not isinstance(payload.get("meta"), dict):
            raise EnergyPriceForecastInvalidResponse("The access metadata is missing.")
        return payload
