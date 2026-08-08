"""Config flow for Energy Price Forecast EU."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    EnergyPriceForecastApi,
    EnergyPriceForecastAuthError,
    EnergyPriceForecastConnectionError,
    EnergyPriceForecastInvalidResponse,
    EnergyPriceForecastRetailUnavailable,
)
from .const import (
    CONF_API_KEY,
    CONF_HORIZON_HOURS,
    CONF_MARKET,
    CONF_POSTAL_CODE,
    CONF_RETAIL_PRICING,
    CONF_WINDOW_HOURS,
    DEFAULT_API_URL,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_WINDOW_HOURS,
    DOMAIN,
    MARKETS,
    PRICES_API_URL,
    RETAIL_MARKETS,
)

_POSTAL_CODE_RE = re.compile(r"^[0-9]{5}$")


def _schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_MARKET, default=defaults.get(CONF_MARKET, "DE")
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=market, label=label)
                        for market, label in MARKETS.items()
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_HORIZON_HOURS,
                default=defaults.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=["24", "48", "72", "120"],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_WINDOW_HOURS,
                default=defaults.get(CONF_WINDOW_HOURS, DEFAULT_WINDOW_HOURS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=24,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_API_KEY, default=""): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_RETAIL_PRICING,
                default=defaults.get(CONF_RETAIL_PRICING, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_POSTAL_CODE, default=defaults.get(CONF_POSTAL_CODE, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        }
    )


def _normalize_input(user_input: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(user_input)
    normalized[CONF_MARKET] = str(normalized[CONF_MARKET]).upper()
    normalized[CONF_HORIZON_HOURS] = int(normalized[CONF_HORIZON_HOURS])
    normalized[CONF_WINDOW_HOURS] = int(normalized[CONF_WINDOW_HOURS])
    api_key = str(normalized.get(CONF_API_KEY, "")).strip()
    if api_key:
        normalized[CONF_API_KEY] = api_key
    else:
        normalized.pop(CONF_API_KEY, None)
    normalized[CONF_RETAIL_PRICING] = bool(normalized.get(CONF_RETAIL_PRICING, False))
    postal_code = str(normalized.get(CONF_POSTAL_CODE, "")).strip()
    if postal_code:
        normalized[CONF_POSTAL_CODE] = postal_code
    else:
        normalized.pop(CONF_POSTAL_CODE, None)
    return normalized


def _validate_retail_selection(data: dict[str, Any]) -> str | None:
    """Check the retail-pricing selection without calling the API.

    Returns an error code for ``errors["base"]``, or None if the selection
    is consistent.
    """
    if not data[CONF_RETAIL_PRICING]:
        return None
    if data[CONF_MARKET] not in RETAIL_MARKETS:
        return "retail_not_supported"
    postal_code = data.get(CONF_POSTAL_CODE)
    if data[CONF_MARKET] == "DE":
        if not postal_code:
            return "postal_code_required"
        if not _POSTAL_CODE_RE.match(postal_code):
            return "invalid_postal_code"
    return None


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    api = EnergyPriceForecastApi(
        session=async_get_clientsession(hass),
        base_url=DEFAULT_API_URL,
        prices_url=PRICES_API_URL,
        market=data[CONF_MARKET],
        horizon_hours=data[CONF_HORIZON_HOURS],
        window_hours=data[CONF_WINDOW_HOURS],
        api_key=data.get(CONF_API_KEY),
    )
    await api.async_get_summary()
    if data[CONF_RETAIL_PRICING]:
        try:
            await api.async_get_retail_prices(data.get(CONF_POSTAL_CODE))
        except (
            EnergyPriceForecastConnectionError,
            EnergyPriceForecastInvalidResponse,
        ) as err:
            raise EnergyPriceForecastRetailUnavailable(str(err)) from err


class EnergyPriceForecastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure Energy Price Forecast EU through the UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        normalized: dict[str, Any] | None = None
        if user_input is not None:
            normalized = _normalize_input(user_input)
            await self.async_set_unique_id(normalized[CONF_MARKET])
            self._abort_if_unique_id_configured()
            retail_error = _validate_retail_selection(normalized)
            if retail_error is not None:
                errors["base"] = retail_error
            else:
                try:
                    await _validate_input(self.hass, normalized)
                except EnergyPriceForecastAuthError:
                    errors["base"] = "invalid_auth"
                except EnergyPriceForecastRetailUnavailable:
                    errors["base"] = "retail_unavailable"
                except EnergyPriceForecastConnectionError:
                    errors["base"] = "cannot_connect"
                except EnergyPriceForecastInvalidResponse:
                    errors["base"] = "invalid_response"
                else:
                    return self.async_create_entry(
                        title=f"Energy Price Forecast EU ({normalized[CONF_MARKET]})",
                        data=normalized,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(normalized or user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        defaults = dict(entry.data)
        defaults[CONF_API_KEY] = ""

        if user_input is not None:
            normalized = _normalize_input(user_input)
            if CONF_API_KEY not in normalized and entry.data.get(CONF_API_KEY):
                normalized[CONF_API_KEY] = entry.data[CONF_API_KEY]
            duplicate = next(
                (
                    candidate
                    for candidate in self._async_current_entries()
                    if candidate.entry_id != entry.entry_id
                    and candidate.unique_id == normalized[CONF_MARKET]
                ),
                None,
            )
            if duplicate is not None:
                errors["base"] = "already_configured"
            else:
                retail_error = _validate_retail_selection(normalized)
                if retail_error is not None:
                    errors["base"] = retail_error
                else:
                    try:
                        await _validate_input(self.hass, normalized)
                    except EnergyPriceForecastAuthError:
                        errors["base"] = "invalid_auth"
                    except EnergyPriceForecastRetailUnavailable:
                        errors["base"] = "retail_unavailable"
                    except EnergyPriceForecastConnectionError:
                        errors["base"] = "cannot_connect"
                    except EnergyPriceForecastInvalidResponse:
                        errors["base"] = "invalid_response"
                    else:
                        return self.async_update_reload_and_abort(
                            entry,
                            unique_id=normalized[CONF_MARKET],
                            data=normalized,
                        )
            defaults.update(user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(defaults),
            errors=errors,
        )
