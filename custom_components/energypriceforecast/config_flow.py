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
from homeassistant.util import dt as dt_util

from .api import (
    EnergyPriceForecastApi,
    EnergyPriceForecastAuthError,
    EnergyPriceForecastConnectionError,
    EnergyPriceForecastInvalidResponse,
    EnergyPriceForecastRetailUnavailable,
    requested_currency,
)
from .const import (
    CONF_API_KEY,
    CONF_CHEAPEST_HOURS_COUNT,
    CONF_CHEAPEST_HOURS_START_HOUR,
    CONF_CHEAPEST_HOURS_WINDOW_HOURS,
    CONF_GREENEST_HOURS_COUNT,
    CONF_HORIZON_HOURS,
    CONF_LOCAL_CURRENCY,
    CONF_MARKET,
    CONF_POSTAL_CODE,
    CONF_RETAIL_FACTOR,
    CONF_RETAIL_PRICING,
    CONF_RETAIL_SOURCE,
    CONF_RETAIL_SURCHARGE,
    CONF_TOU_LOW_END,
    CONF_TOU_LOW_START,
    CONF_TOU_OPERATOR,
    CONF_TOU_PEAK_END,
    CONF_TOU_PEAK_START,
    CONF_TOU_RATE_LOW,
    CONF_TOU_RATE_PEAK,
    CONF_TOU_RATE_STANDARD,
    CONF_TOU_SOURCE,
    CONF_TOU_TARIFF,
    CONF_TOU_WEEKEND,
    CONF_TOU_WINTER,
    CONF_TOU_WINTER_FROM,
    CONF_TOU_WINTER_RATE_LOW,
    CONF_TOU_WINTER_RATE_PEAK,
    CONF_TOU_WINTER_RATE_STANDARD,
    CONF_TOU_WINTER_TO,
    CONF_UPDATE_INTERVAL_MINUTES,
    CONF_WEEKEND_HOURS_COUNT,
    CONF_WINDOW_HOURS,
    DEFAULT_API_URL,
    DEFAULT_CHEAPEST_HOURS_COUNT,
    DEFAULT_CHEAPEST_HOURS_START_HOUR,
    DEFAULT_CHEAPEST_HOURS_WINDOW_HOURS,
    DEFAULT_GREENEST_HOURS_COUNT,
    DEFAULT_HORIZON_HOURS,
    DEFAULT_LOCAL_CURRENCY,
    DEFAULT_RETAIL_FACTOR,
    DEFAULT_RETAIL_SURCHARGE,
    DEFAULT_TOU_WINTER_FROM,
    DEFAULT_TOU_WINTER_TO,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DEFAULT_WEEKEND_HOURS_COUNT,
    DEFAULT_WINDOW_HOURS,
    DOMAIN,
    HORIZON_HOURS_OPTIONS,
    DATAHUB_MARKETS,
    MARKETS,
    MAX_CHEAPEST_HOURS_COUNT,
    MAX_CHEAPEST_HOURS_WINDOW_HOURS,
    MAX_GREENEST_HOURS_COUNT,
    MAX_RETAIL_FACTOR,
    MAX_RETAIL_SURCHARGE,
    MAX_TOU_RATE,
    MAX_UPDATE_INTERVAL_MINUTES,
    MAX_WEEKEND_HOURS_COUNT,
    MIN_CHEAPEST_HOURS_WINDOW_HOURS,
    MIN_RETAIL_FACTOR,
    MIN_RETAIL_SURCHARGE,
    MIN_TOU_RATE,
    MIN_UPDATE_INTERVAL_MINUTES,
    PRICES_API_URL,
    RETAIL_MARKETS,
    RETAIL_SOURCE_ESTIMATE,
    RETAIL_SOURCE_FORMULA,
    RETAIL_SOURCE_OFF,
    PREFILL_MARKETS,
    RETAIL_SOURCES,
    TOU_SOURCE_DATAHUB,
    TOU_SOURCE_MANUAL,
    TOU_SOURCE_OFF,
    TOU_SOURCES,
)
from .dk_datahub import DatahubError, DatahubTariff, async_list_household_tariffs
from .no_frinettleie import (
    ATTRIBUTION,
    CollectionUnavailable,
    Operator,
    async_load_operators,
    prefill,
)
from .time_of_use import WEEKEND_RULES, in_window

_POSTAL_CODE_RE = re.compile(r"^[0-9]{5}$")


def _horizon_default(defaults: dict[str, Any]) -> str:
    """Return the stored horizon as a valid dropdown value.

    An entry written before 168 was withdrawn still holds it, and voluptuous
    validates a Required field's default whenever the key is missing from the
    input - so an unknown value here would make the form raise instead of
    render. Entries are migrated on load, but a stale one must still show a
    usable form rather than a crash.
    """
    stored = defaults.get(CONF_HORIZON_HOURS, DEFAULT_HORIZON_HOURS)
    try:
        hours = int(stored)
    except (TypeError, ValueError):
        return str(DEFAULT_HORIZON_HOURS)
    if hours in HORIZON_HOURS_OPTIONS:
        return str(hours)
    # Fall back to the largest offered horizon that does not exceed the
    # stored one, so a withdrawn 168 becomes 120 rather than the default 48.
    lower = [option for option in HORIZON_HOURS_OPTIONS if option <= hours]
    return str(max(lower)) if lower else str(min(HORIZON_HOURS_OPTIONS))


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
                default=_horizon_default(defaults),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(
                            value=str(hours),
                            label=(
                                f"{hours} (API-Key)"
                                if hours > DEFAULT_HORIZON_HOURS
                                else str(hours)
                            ),
                        )
                        for hours in HORIZON_HOURS_OPTIONS
                    ],
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
                CONF_RETAIL_SOURCE,
                default=defaults.get(CONF_RETAIL_SOURCE, RETAIL_SOURCE_OFF),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(RETAIL_SOURCES),
                    translation_key=CONF_RETAIL_SOURCE,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_POSTAL_CODE, default=defaults.get(CONF_POSTAL_CODE, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            # Shown to every market, like the postal code: the form cannot
            # change with the selection above, so the descriptions say which
            # fields belong to which source.
            # Money is entered to whatever precision the contract states, and
            # Home Assistant refuses a numeric step below 0.001: "any" is the
            # only step that neither blocks 0.1350 nor marks it invalid.
            vol.Optional(
                CONF_RETAIL_FACTOR,
                default=defaults.get(CONF_RETAIL_FACTOR, DEFAULT_RETAIL_FACTOR),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_RETAIL_FACTOR,
                    max=MAX_RETAIL_FACTOR,
                    step="any",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_RETAIL_SURCHARGE,
                default=defaults.get(CONF_RETAIL_SURCHARGE, DEFAULT_RETAIL_SURCHARGE),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_RETAIL_SURCHARGE,
                    max=MAX_RETAIL_SURCHARGE,
                    step="any",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            # A checkbox, not a currency dropdown: every market sees the same
            # form, and a free choice would allow pairs like Germany in Swedish
            # krona. "My market's own currency" cannot be set wrong.
            vol.Optional(
                CONF_TOU_SOURCE,
                default=defaults.get(CONF_TOU_SOURCE, TOU_SOURCE_OFF),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(TOU_SOURCES),
                    translation_key=CONF_TOU_SOURCE,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_LOCAL_CURRENCY,
                default=defaults.get(CONF_LOCAL_CURRENCY, DEFAULT_LOCAL_CURRENCY),
            ): BooleanSelector(),
            vol.Optional(
                CONF_UPDATE_INTERVAL_MINUTES,
                default=defaults.get(
                    CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_UPDATE_INTERVAL_MINUTES,
                    max=MAX_UPDATE_INTERVAL_MINUTES,
                    step=5,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_CHEAPEST_HOURS_COUNT,
                default=defaults.get(
                    CONF_CHEAPEST_HOURS_COUNT, DEFAULT_CHEAPEST_HOURS_COUNT
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_CHEAPEST_HOURS_COUNT,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_CHEAPEST_HOURS_WINDOW_HOURS,
                default=defaults.get(
                    CONF_CHEAPEST_HOURS_WINDOW_HOURS,
                    DEFAULT_CHEAPEST_HOURS_WINDOW_HOURS,
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_CHEAPEST_HOURS_WINDOW_HOURS,
                    max=MAX_CHEAPEST_HOURS_WINDOW_HOURS,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_CHEAPEST_HOURS_START_HOUR,
                default=defaults.get(
                    CONF_CHEAPEST_HOURS_START_HOUR, DEFAULT_CHEAPEST_HOURS_START_HOUR
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=23,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_WEEKEND_HOURS_COUNT,
                default=defaults.get(
                    CONF_WEEKEND_HOURS_COUNT, DEFAULT_WEEKEND_HOURS_COUNT
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_WEEKEND_HOURS_COUNT,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_GREENEST_HOURS_COUNT,
                default=defaults.get(
                    CONF_GREENEST_HOURS_COUNT, DEFAULT_GREENEST_HOURS_COUNT
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=MAX_GREENEST_HOURS_COUNT,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _windows_schema(defaults: dict[str, Any]) -> vol.Schema:
    """When the network is cheap, when it is expensive, and in which months."""
    hours = NumberSelectorConfig(min=0, max=23, step=1, mode=NumberSelectorMode.BOX)
    months = NumberSelectorConfig(min=1, max=12, step=1, mode=NumberSelectorMode.BOX)
    return vol.Schema(
        {
            vol.Optional(
                CONF_TOU_LOW_START, default=int(defaults.get(CONF_TOU_LOW_START, 0))
            ): NumberSelector(hours),
            vol.Optional(
                CONF_TOU_LOW_END, default=int(defaults.get(CONF_TOU_LOW_END, 0))
            ): NumberSelector(hours),
            vol.Optional(
                CONF_TOU_PEAK_START, default=int(defaults.get(CONF_TOU_PEAK_START, 0))
            ): NumberSelector(hours),
            vol.Optional(
                CONF_TOU_PEAK_END, default=int(defaults.get(CONF_TOU_PEAK_END, 0))
            ): NumberSelector(hours),
            vol.Optional(
                CONF_TOU_WEEKEND,
                default=str(defaults.get(CONF_TOU_WEEKEND, WEEKEND_RULES[0])),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(WEEKEND_RULES),
                    translation_key=CONF_TOU_WEEKEND,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_TOU_WINTER, default=bool(defaults.get(CONF_TOU_WINTER, False))
            ): BooleanSelector(),
            vol.Optional(
                CONF_TOU_WINTER_FROM,
                default=int(
                    defaults.get(CONF_TOU_WINTER_FROM, DEFAULT_TOU_WINTER_FROM)
                    or DEFAULT_TOU_WINTER_FROM
                ),
            ): NumberSelector(months),
            vol.Optional(
                CONF_TOU_WINTER_TO,
                default=int(
                    defaults.get(CONF_TOU_WINTER_TO, DEFAULT_TOU_WINTER_TO)
                    or DEFAULT_TOU_WINTER_TO
                ),
            ): NumberSelector(months),
        }
    )


def _rate_field(defaults: dict[str, Any], key: str) -> Any:
    return vol.Optional(key, default=float(defaults.get(key, 0.0)))


def _rates_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Only the amounts the windows chosen in the step before actually use."""
    rate = NumberSelector(
        NumberSelectorConfig(
            min=MIN_TOU_RATE, max=MAX_TOU_RATE, step="any", mode=NumberSelectorMode.BOX
        )
    )
    has_peak = int(defaults.get(CONF_TOU_PEAK_START, 0)) != int(
        defaults.get(CONF_TOU_PEAK_END, 0)
    )
    fields: dict[Any, Any] = {
        _rate_field(defaults, CONF_TOU_RATE_LOW): rate,
        _rate_field(defaults, CONF_TOU_RATE_STANDARD): rate,
    }
    if has_peak:
        fields[_rate_field(defaults, CONF_TOU_RATE_PEAK)] = rate
    if defaults.get(CONF_TOU_WINTER):
        fields[_rate_field(defaults, CONF_TOU_WINTER_RATE_LOW)] = rate
        fields[_rate_field(defaults, CONF_TOU_WINTER_RATE_STANDARD)] = rate
        if has_peak:
            fields[_rate_field(defaults, CONF_TOU_WINTER_RATE_PEAK)] = rate
    return vol.Schema(fields)


def _normalize_windows(user_input: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(user_input)
    for key in (
        CONF_TOU_LOW_START,
        CONF_TOU_LOW_END,
        CONF_TOU_PEAK_START,
        CONF_TOU_PEAK_END,
        CONF_TOU_WINTER_FROM,
        CONF_TOU_WINTER_TO,
    ):
        if key in normalized:
            normalized[key] = int(normalized[key])
    normalized[CONF_TOU_WINTER] = bool(normalized.get(CONF_TOU_WINTER, False))
    return normalized


def _normalize_rates(user_input: dict[str, Any]) -> dict[str, Any]:
    return {key: float(value) for key, value in user_input.items()}


def _price_unit(data: dict[str, Any]) -> str:
    """The unit the price sensors show - the unit the amounts are typed in."""
    market = str(data.get(CONF_MARKET, ""))
    currency = requested_currency(market, bool(data.get(CONF_LOCAL_CURRENCY)))
    if currency:
        return f"{currency}/kWh"
    # Denmark and Norway are answered in their own currency without asking.
    if market.startswith("DK"):
        return "DKK/kWh"
    if market.startswith("NO"):
        return "NOK/kWh"
    return "EUR/kWh"


def _validate_windows(data: dict[str, Any]) -> str | None:
    """The two windows must not claim the same hour.

    They would not crash - the cheap one is checked first - but an hour that
    is listed as both cheap and expensive means the user misread their price
    sheet, and silently picking one of the two would hide that.
    """
    low = (int(data.get(CONF_TOU_LOW_START, 0)), int(data.get(CONF_TOU_LOW_END, 0)))
    peak = (int(data.get(CONF_TOU_PEAK_START, 0)), int(data.get(CONF_TOU_PEAK_END, 0)))
    if low[0] == low[1] or peak[0] == peak[1]:
        return None
    if any(in_window(hour, *low) and in_window(hour, *peak) for hour in range(24)):
        return "tou_windows_overlap"
    return None


def _validate_tariff_selection(data: dict[str, Any]) -> str | None:
    """Where the network tariff may come from, and with which retail source."""
    source = data.get(CONF_TOU_SOURCE, TOU_SOURCE_OFF)
    if source == TOU_SOURCE_OFF:
        return None
    if data.get(CONF_RETAIL_SOURCE) != RETAIL_SOURCE_FORMULA:
        # The API's estimate already contains a grid fee of its own; adding
        # a second one would count the network twice.
        return "tou_needs_formula"
    if source == TOU_SOURCE_DATAHUB and data.get(CONF_MARKET) not in DATAHUB_MARKETS:
        return "tou_not_supported"
    return None


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
    source = str(normalized.get(CONF_RETAIL_SOURCE, RETAIL_SOURCE_OFF))
    normalized[CONF_RETAIL_SOURCE] = (
        source if source in RETAIL_SOURCES else RETAIL_SOURCE_OFF
    )
    normalized[CONF_RETAIL_FACTOR] = float(
        normalized.get(CONF_RETAIL_FACTOR, DEFAULT_RETAIL_FACTOR)
    )
    normalized[CONF_RETAIL_SURCHARGE] = float(
        normalized.get(CONF_RETAIL_SURCHARGE, DEFAULT_RETAIL_SURCHARGE)
    )
    # The checkbox this replaced lives on only in the migration.
    normalized.pop(CONF_RETAIL_PRICING, None)
    source = str(normalized.get(CONF_TOU_SOURCE, TOU_SOURCE_OFF))
    normalized[CONF_TOU_SOURCE] = (
        source if source in TOU_SOURCES else TOU_SOURCE_OFF
    )
    normalized[CONF_LOCAL_CURRENCY] = bool(
        normalized.get(CONF_LOCAL_CURRENCY, DEFAULT_LOCAL_CURRENCY)
    )
    postal_code = str(normalized.get(CONF_POSTAL_CODE, "")).strip()
    if postal_code:
        normalized[CONF_POSTAL_CODE] = postal_code
    else:
        normalized.pop(CONF_POSTAL_CODE, None)
    normalized[CONF_UPDATE_INTERVAL_MINUTES] = int(
        normalized.get(CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES)
    )
    normalized[CONF_CHEAPEST_HOURS_COUNT] = int(
        normalized.get(CONF_CHEAPEST_HOURS_COUNT, DEFAULT_CHEAPEST_HOURS_COUNT)
    )
    normalized[CONF_CHEAPEST_HOURS_WINDOW_HOURS] = int(
        normalized.get(
            CONF_CHEAPEST_HOURS_WINDOW_HOURS, DEFAULT_CHEAPEST_HOURS_WINDOW_HOURS
        )
    )
    normalized[CONF_CHEAPEST_HOURS_START_HOUR] = int(
        normalized.get(
            CONF_CHEAPEST_HOURS_START_HOUR, DEFAULT_CHEAPEST_HOURS_START_HOUR
        )
    )
    normalized[CONF_WEEKEND_HOURS_COUNT] = int(
        normalized.get(CONF_WEEKEND_HOURS_COUNT, DEFAULT_WEEKEND_HOURS_COUNT)
    )
    normalized[CONF_GREENEST_HOURS_COUNT] = int(
        normalized.get(CONF_GREENEST_HOURS_COUNT, DEFAULT_GREENEST_HOURS_COUNT)
    )
    return normalized


def _validate_retail_selection(data: dict[str, Any]) -> str | None:
    """Check the retail-pricing selection without calling the API.

    Returns an error code for ``errors["base"]``, or None if the selection
    is consistent.
    """
    source = data.get(CONF_RETAIL_SOURCE, RETAIL_SOURCE_OFF)
    if source == RETAIL_SOURCE_OFF:
        return None
    if source == RETAIL_SOURCE_FORMULA:
        # Every market can use a formula. A factor of 0 or below would make
        # every hour cost the same or turn the ranking upside down.
        if not data.get(CONF_RETAIL_FACTOR, DEFAULT_RETAIL_FACTOR) > 0:
            return "invalid_retail_factor"
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


def _validate_cheapest_hours_selection(data: dict[str, Any]) -> str | None:
    """Check that the cheapest-hours count fits inside its own block.

    Picking e.g. 10 cheapest hours out of an 8-hour block can never be
    satisfied, so this is rejected here rather than silently clamped.
    """
    if data[CONF_CHEAPEST_HOURS_COUNT] > data[CONF_CHEAPEST_HOURS_WINDOW_HOURS]:
        return "cheapest_hours_exceeds_window"
    # The greenest hours are picked from the same block, so the same ceiling
    # applies to them. Read with a default: an entry saved before this option
    # existed simply has no such count, which means the plan is off.
    if (
        data.get(CONF_GREENEST_HOURS_COUNT, 0)
        > data[CONF_CHEAPEST_HOURS_WINDOW_HOURS]
    ):
        return "greenest_hours_exceeds_window"
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
        currency=requested_currency(
            data[CONF_MARKET],
            data.get(CONF_LOCAL_CURRENCY, DEFAULT_LOCAL_CURRENCY),
        ),
    )
    await api.async_get_summary()
    # Only the estimate comes from the API and can fail there; the formula is
    # computed from the base series the summary check above already reached.
    if data.get(CONF_RETAIL_SOURCE) == RETAIL_SOURCE_ESTIMATE:
        try:
            await api.async_get_prices(
                price_mode="retail", postal_code=data.get(CONF_POSTAL_CODE)
            )
        except (
            EnergyPriceForecastConnectionError,
            EnergyPriceForecastInvalidResponse,
        ) as err:
            raise EnergyPriceForecastRetailUnavailable(str(err)) from err


# The keys each tariff source keeps. Everything else of the tariff is dropped
# on save, so an entry never carries amounts from a source it no longer uses.
_MANUAL_KEYS = frozenset(
    {
        CONF_TOU_LOW_START,
        CONF_TOU_LOW_END,
        CONF_TOU_PEAK_START,
        CONF_TOU_PEAK_END,
        CONF_TOU_WEEKEND,
        CONF_TOU_RATE_LOW,
        CONF_TOU_RATE_STANDARD,
        CONF_TOU_RATE_PEAK,
        CONF_TOU_WINTER,
        CONF_TOU_WINTER_FROM,
        CONF_TOU_WINTER_TO,
        CONF_TOU_WINTER_RATE_LOW,
        CONF_TOU_WINTER_RATE_STANDARD,
        CONF_TOU_WINTER_RATE_PEAK,
    }
)
_TARIFF_KEYS = _MANUAL_KEYS | {CONF_TOU_TARIFF}
_KEPT_TARIFF_KEYS = {
    TOU_SOURCE_OFF: frozenset(),
    TOU_SOURCE_MANUAL: _MANUAL_KEYS,
    TOU_SOURCE_DATAHUB: frozenset({CONF_TOU_TARIFF}),
}


class EnergyPriceForecastConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure Energy Price Forecast EU through the UI."""

    # 2: horizons above MAX_HORIZON_HOURS are clamped - 168 was offered for a
    #    while although the forecast never produced more than 120 hours.
    # 3: the retail checkbox became a choice of source: off, the API's
    #    estimate, or the user's own formula.
    # The network tariff of 1.7.0 only added keys, and a missing key means
    # "off", so it needs no version of its own - and 1.6.0 still loads the
    # entry after a rollback, ignoring what it does not know.
    VERSION = 3

    def __init__(self) -> None:
        # What the steps have collected; saved as one entry at the end.
        self._data: dict[str, Any] = {}
        # When reconfiguring: the entry the last step updates.
        self._entry: config_entries.ConfigEntry | None = None
        # Fetched once per dialog, not on every redisplay of a step.
        self._tariffs: list[DatahubTariff] | None = None
        self._operators: list[Operator] | None = None
        self._prefill_note = ""

    async def _async_check(self, data: dict[str, Any]) -> str | None:
        """Reach the API with these settings, and name what went wrong."""
        try:
            await _validate_input(self.hass, data)
        except EnergyPriceForecastAuthError:
            return "invalid_auth"
        except EnergyPriceForecastRetailUnavailable:
            return "retail_unavailable"
        except EnergyPriceForecastConnectionError:
            return "cannot_connect"
        except EnergyPriceForecastInvalidResponse:
            return "invalid_response"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        normalized: dict[str, Any] | None = None
        if user_input is not None:
            normalized = _normalize_input(user_input)
            await self.async_set_unique_id(normalized[CONF_MARKET])
            self._abort_if_unique_id_configured()
            error = (
                _validate_retail_selection(normalized)
                or _validate_tariff_selection(normalized)
                or _validate_cheapest_hours_selection(normalized)
                or await self._async_check(normalized)
            )
            if error is None:
                self._data = normalized
                return await self._async_next_step()
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(normalized or user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        self._entry = entry
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
                error = (
                    _validate_retail_selection(normalized)
                    or _validate_tariff_selection(normalized)
                    or _validate_cheapest_hours_selection(normalized)
                    or await self._async_check(normalized)
                )
                if error is None:
                    # The tariff's own fields are asked in the steps after
                    # this one; what is stored now is what they start from.
                    stored = {k: v for k, v in entry.data.items() if k in _TARIFF_KEYS}
                    self._data = {**stored, **normalized}
                    return await self._async_next_step()
                errors["base"] = error
            defaults.update(user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(defaults),
            errors=errors,
        )

    async def _async_next_step(self) -> ConfigFlowResult:
        """Ask for the network tariff if one was chosen, otherwise save."""
        source = self._data.get(CONF_TOU_SOURCE, TOU_SOURCE_OFF)
        if source == TOU_SOURCE_DATAHUB:
            return await self.async_step_tariff_lookup()
        if source == TOU_SOURCE_MANUAL:
            if self._data.get(CONF_MARKET) in PREFILL_MARKETS:
                return await self.async_step_tariff_prefill()
            return await self.async_step_tariff_windows()
        return self._async_save()

    def _async_save(self) -> ConfigFlowResult:
        source = self._data.get(CONF_TOU_SOURCE, TOU_SOURCE_OFF)
        kept = _KEPT_TARIFF_KEYS.get(source, frozenset())
        data = {
            key: value
            for key, value in self._data.items()
            if key not in _TARIFF_KEYS or key in kept
        }
        if self._entry is not None:
            return self.async_update_reload_and_abort(
                self._entry, unique_id=data[CONF_MARKET], data=data
            )
        return self.async_create_entry(
            title=f"Energy Price Forecast EU ({data[CONF_MARKET]})", data=data
        )

    async def async_step_tariff_prefill(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Norway: start from the collected tariff of the user's operator."""
        if user_input is not None:
            chosen = next(
                (
                    operator
                    for operator in self._operators or []
                    if operator.gln == user_input.get(CONF_TOU_OPERATOR)
                ),
                None,
            )
            if chosen is not None:
                self._data.update(prefill(chosen))
                self._prefill_note = f"{chosen.name}, {chosen.updated}"
            return await self.async_step_tariff_windows()

        if self._operators is None:
            try:
                self._operators = await async_load_operators(
                    async_get_clientsession(self.hass),
                    str(self._data.get(CONF_MARKET, "")),
                )
            except CollectionUnavailable:
                self._operators = []
        if not self._operators:
            # Nothing to start from: the fields are typed in by hand.
            return await self.async_step_tariff_windows()

        options: list[SelectOptionDict] = []
        seen: set[str] = set()
        for operator in self._operators:
            if operator.gln in seen:
                continue
            seen.add(operator.gln)
            options.append(
                SelectOptionDict(
                    value=operator.gln,
                    label=f"{operator.name} ({operator.updated})"
                    # Seasonal or several special periods: prefilled only
                    # in part, which the step's description explains.
                    + (" \u26a0" if operator.uncertain else ""),
                )
            )
        return self.async_show_form(
            step_id="tariff_prefill",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_TOU_OPERATOR): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            description_placeholders={"source": ATTRIBUTION},
        )

    async def async_step_tariff_windows(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """When the network is cheap, when it is expensive, and in which months."""
        errors: dict[str, str] = {}
        if user_input is not None:
            windows = _normalize_windows(user_input)
            error = _validate_windows(windows)
            if error is None:
                self._data.update(windows)
                return await self.async_step_tariff_rates()
            errors["base"] = error
        return self.async_show_form(
            step_id="tariff_windows",
            data_schema=_windows_schema({**self._data, **(user_input or {})}),
            errors=errors,
            description_placeholders={"prefill": self._prefill_note or "-"},
        )

    async def async_step_tariff_rates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The amounts, for exactly the windows and seasons chosen before."""
        if user_input is not None:
            self._data.update(_normalize_rates(user_input))
            return self._async_save()
        return self.async_show_form(
            step_id="tariff_rates",
            data_schema=_rates_schema(self._data),
            description_placeholders={"unit": _price_unit(self._data)},
        )

    async def async_step_tariff_lookup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Denmark: the operator's published tariff, looked up once a day."""
        if user_input and user_input.get(CONF_TOU_TARIFF):
            self._data[CONF_TOU_TARIFF] = user_input[CONF_TOU_TARIFF]
            return self._async_save()

        if self._tariffs is None:
            try:
                self._tariffs = await async_list_household_tariffs(
                    async_get_clientsession(self.hass), dt_util.now().date()
                )
            except DatahubError:
                self._tariffs = None
        if not self._tariffs:
            # Nothing to choose from. Submitting this empty form asks again.
            self._tariffs = None
            return self.async_show_form(
                step_id="tariff_lookup",
                data_schema=vol.Schema({}),
                errors={"base": "datahub_unavailable"},
            )

        options = [
            SelectOptionDict(value=tariff.value, label=tariff.label)
            for tariff in self._tariffs
        ]
        stored = self._data.get(CONF_TOU_TARIFF)
        field = (
            vol.Required(CONF_TOU_TARIFF, default=stored)
            if stored in {option["value"] for option in options}
            else vol.Required(CONF_TOU_TARIFF)
        )
        return self.async_show_form(
            step_id="tariff_lookup",
            data_schema=vol.Schema(
                {
                    field: SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )
