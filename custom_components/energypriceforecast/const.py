"""Constants for Energy Price Forecast EU."""

from typing import Final

DOMAIN: Final = "energypriceforecast"
NAME: Final = "Energy Price Forecast EU"

# Sent as the User-Agent, so the API can tell which version a request came
# from. Must equal manifest.json's version - test_version.py enforces that,
# because this drifted to 0.1.0 for ten releases and every request from
# every user was mislabelled the whole time.
VERSION: Final = "1.8.0"
DEFAULT_API_URL: Final = (
    "https://api.energypriceforecast.eu/api/v1/home-assistant/summary"
)
PRICES_API_URL: Final = (
    "https://api.energypriceforecast.eu/api/v1/home-assistant/prices"
)
DEFAULT_HORIZON_HOURS: Final = 48

# The horizons the config flow offers, and the ceiling everything else clamps
# to. The API accepts hours=168 as a request parameter, but the forecast
# itself never produces more than 120 hours of data. Offering more would
# promise coverage that never arrives, and the "Allowed horizon" diagnostic
# sensor would then contradict the value the user just picked.
HORIZON_HOURS_OPTIONS: Final[tuple[int, ...]] = (24, 48, 72, 120)
MAX_HORIZON_HOURS: Final = 120

DEFAULT_WINDOW_HOURS: Final = 4
DEFAULT_UPDATE_INTERVAL_MINUTES: Final = 30
MIN_UPDATE_INTERVAL_MINUTES: Final = 15
MAX_UPDATE_INTERVAL_MINUTES: Final = 120

CONF_MARKET: Final = "market"
CONF_HORIZON_HOURS: Final = "horizon_hours"
CONF_WINDOW_HOURS: Final = "window_hours"
CONF_API_KEY: Final = "api_key"
# Until 1.6.0 retail pricing was a checkbox. Only the migration still reads it.
CONF_RETAIL_PRICING: Final = "retail_pricing"
# Where the retail price comes from: nowhere, the API's estimate, or the
# user's own formula (day-ahead x factor + surcharge, see retail_formula.py).
CONF_RETAIL_SOURCE: Final = "retail_source"
RETAIL_SOURCE_OFF: Final = "off"
RETAIL_SOURCE_ESTIMATE: Final = "estimate"
RETAIL_SOURCE_FORMULA: Final = "formula"
RETAIL_SOURCES: Final[tuple[str, ...]] = (
    RETAIL_SOURCE_OFF,
    RETAIL_SOURCE_ESTIMATE,
    RETAIL_SOURCE_FORMULA,
)
CONF_RETAIL_FACTOR: Final = "retail_factor"
DEFAULT_RETAIL_FACTOR: Final = 1.0
# Above 0, or the order of the hours - and every plan built on it - would
# stop meaning anything. 10 leaves room for any VAT and then some.
MIN_RETAIL_FACTOR: Final = 0.01
MAX_RETAIL_FACTOR: Final = 10.0
# Per kWh including VAT, in the unit of the price sensors. Wide enough for
# koruna and krona as well as euro, and negative for contracts with a
# discount on the day-ahead price.
CONF_RETAIL_SURCHARGE: Final = "retail_surcharge"
DEFAULT_RETAIL_SURCHARGE: Final = 0.0
MIN_RETAIL_SURCHARGE: Final = -100.0
MAX_RETAIL_SURCHARGE: Final = 100.0

# Grid charges that change with the time of day, new in 1.7.0. They belong
# inside the factor, because a price sheet states them without VAT - see
# time_of_use.py, which also lists which markets have them.
CONF_TOU_SOURCE: Final = "tou_source"
TOU_SOURCE_OFF: Final = "off"
TOU_SOURCE_MANUAL: Final = "manual"
# Denmark is the one market that publishes every operator's tariff in
# machine-readable form, so there it can be looked up instead of typed.
TOU_SOURCE_DATAHUB: Final = "datahub"
TOU_SOURCES: Final[tuple[str, ...]] = (
    TOU_SOURCE_OFF,
    TOU_SOURCE_MANUAL,
    TOU_SOURCE_DATAHUB,
)
# Where the published Danish tariffs apply, and where the Norwegian
# collection can prefill the fields the user then checks.
DATAHUB_MARKETS: Final[frozenset[str]] = frozenset({"DK1", "DK2"})
PREFILL_MARKETS: Final[frozenset[str]] = frozenset(
    {"NO1", "NO2", "NO3", "NO4", "NO5"}
)

CONF_TOU_LOW_START: Final = "tou_low_start"
CONF_TOU_LOW_END: Final = "tou_low_end"
CONF_TOU_PEAK_START: Final = "tou_peak_start"
CONF_TOU_PEAK_END: Final = "tou_peak_end"
CONF_TOU_WEEKEND: Final = "tou_weekend"
CONF_TOU_RATE_LOW: Final = "tou_rate_low"
CONF_TOU_RATE_STANDARD: Final = "tou_rate_standard"
CONF_TOU_RATE_PEAK: Final = "tou_rate_peak"
CONF_TOU_WINTER: Final = "tou_winter"
CONF_TOU_WINTER_FROM: Final = "tou_winter_from"
CONF_TOU_WINTER_TO: Final = "tou_winter_to"
CONF_TOU_WINTER_RATE_LOW: Final = "tou_winter_rate_low"
CONF_TOU_WINTER_RATE_STANDARD: Final = "tou_winter_rate_standard"
CONF_TOU_WINTER_RATE_PEAK: Final = "tou_winter_rate_peak"
CONF_TOU_TARIFF: Final = "tou_tariff"
# Only asked in the dialog, never stored: which Norwegian operator's
# collected tariff the fields should start from.
CONF_TOU_OPERATOR: Final = "tou_operator"

# Per kWh, without VAT, in the unit of the price sensors. A network charge
# is never negative, but a discount the operator books separately can be,
# and Danish operators do exactly that.
MIN_TOU_RATE: Final = -10.0
MAX_TOU_RATE: Final = 10.0
DEFAULT_TOU_WINTER_FROM: Final = 11
DEFAULT_TOU_WINTER_TO: Final = 3

# A tariff window is stated in the market's own clock, not in the clock of
# whoever configured Home Assistant, and not every market is on Central
# European Time: Portugal is an hour behind it, Finland, Greece, Bulgaria and
# Romania an hour ahead. Each entry is the zone the API itself reports for
# that market, so a window of "22 to 6" means the same hours here as on the
# grid operator's price sheet. test_markets.py keeps the list complete.
MARKET_TIME_ZONES: Final[dict[str, str]] = {
    "AT": "Europe/Vienna",
    "BE": "Europe/Brussels",
    "BG": "Europe/Sofia",
    "CH": "Europe/Zurich",
    "CZ": "Europe/Prague",
    "DE": "Europe/Berlin",
    "DK1": "Europe/Copenhagen",
    "DK2": "Europe/Copenhagen",
    "ES": "Europe/Madrid",
    "FI": "Europe/Helsinki",
    "FR": "Europe/Paris",
    "GR": "Europe/Athens",
    "ITN": "Europe/Rome",
    "IT_CNOR": "Europe/Rome",
    "IT_CSUD": "Europe/Rome",
    "IT_SUD": "Europe/Rome",
    "IT_CALA": "Europe/Rome",
    "IT_SICI": "Europe/Rome",
    "IT_SARD": "Europe/Rome",
    "NL": "Europe/Amsterdam",
    "NO1": "Europe/Oslo",
    "NO2": "Europe/Oslo",
    "NO3": "Europe/Oslo",
    "NO4": "Europe/Oslo",
    "NO5": "Europe/Oslo",
    "PL": "Europe/Warsaw",
    "PT": "Europe/Lisbon",
    "RO": "Europe/Bucharest",
    "SE1": "Europe/Stockholm",
    "SE2": "Europe/Stockholm",
    "SE3": "Europe/Stockholm",
    "SE4": "Europe/Stockholm",
    "SK": "Europe/Bratislava",
}
# Only reached by a market added to MARKETS without a zone, which the tests
# refuse - and by then Central European Time is the likeliest guess.
DEFAULT_TIME_ZONE: Final = "Europe/Brussels"
CONF_POSTAL_CODE: Final = "postal_code"
CONF_LOCAL_CURRENCY: Final = "local_currency"
DEFAULT_LOCAL_CURRENCY: Final = False
CONF_UPDATE_INTERVAL_MINUTES: Final = "update_interval_minutes"
CONF_CHEAPEST_HOURS_COUNT: Final = "cheapest_hours_count"
DEFAULT_CHEAPEST_HOURS_COUNT: Final = 0
MAX_CHEAPEST_HOURS_COUNT: Final = 48

# The cheapest-hours block: a fixed, repeating period of CONF_..._WINDOW_HOURS
# hours, anchored to local midnight plus CONF_..._START_HOUR. CONF_..._COUNT
# hours are picked once per block and never re-picked once published, even if
# a later forecast update would rank them differently - see planning.py.
CONF_CHEAPEST_HOURS_WINDOW_HOURS: Final = "cheapest_hours_window_hours"
DEFAULT_CHEAPEST_HOURS_WINDOW_HOURS: Final = 24
MIN_CHEAPEST_HOURS_WINDOW_HOURS: Final = 2
# A block can never be longer than the forecast that has to cover it, or no
# plan could ever be published for it.
MAX_CHEAPEST_HOURS_WINDOW_HOURS: Final = MAX_HORIZON_HOURS
CONF_CHEAPEST_HOURS_START_HOUR: Final = "cheapest_hours_start_hour"
DEFAULT_CHEAPEST_HOURS_START_HOUR: Final = 0

# The weekend plan: a separate, fixed Saturday 00:00 - Monday 00:00 block for
# loads that are specifically flexible on weekends (e.g. EV charging), picked
# and locked the same way as the regular cheapest-hours block.
CONF_WEEKEND_HOURS_COUNT: Final = "weekend_hours_count"
DEFAULT_WEEKEND_HOURS_COUNT: Final = 0
MAX_WEEKEND_HOURS_COUNT: Final = 48

# The greenest-hours plan: the same block as the cheapest-hours one, but
# picked on CO2 intensity instead of price. It deliberately shares the block
# length and start hour: how flexible a load is, is a property of the
# household, not of the number being optimised - and two independent block
# geometries would double the settings for a distinction nobody makes.
CONF_GREENEST_HOURS_COUNT: Final = "greenest_hours_count"
DEFAULT_GREENEST_HOURS_COUNT: Final = 0
MAX_GREENEST_HOURS_COUNT: Final = 48

# The combined window's score arrives as 0..1 with 0 being the best window in
# the horizon - an internal ranking key that was exposed unchanged and read
# backwards by everyone. Rescaled to 0..100 with 100 being best.
COMBINED_SCORE_SCALE: Final = 100

# The combined score for now (1.5.0), see scoring.py. Its reference is the
# next 24 hours, but only as far as prices are published: over 30 days the
# frozen forecast for tomorrow's early hours ran 1.5-4.3 ct/kWh too low in
# 11 of 12 markets, enough to move the score by 18-24 points at noon in Norway.
COMBINED_SCORE_REFERENCE_HOURS: Final = 24
# Just before the auction the published prices end at midnight; fewer hours
# than this are too few to rank the present against.
COMBINED_SCORE_MIN_REFERENCE_HOURS: Final = 4
# A spread below these counts as none, so a quantity that barely moves barely
# moves the ranking. Chosen, not derived: a hydro grid sits at 20-21 g all
# day, and without a floor a tenth of a gram swung the score by 25 points.
COMBINED_SCORE_CO2_FLOOR_G_KWH: Final = 25.0
COMBINED_SCORE_PRICE_FLOOR_SHARE: Final = 0.10

PLATFORMS: Final = ["sensor", "binary_sensor"]

# Markets where the API can compute an assumption-based retail (all-in)
# price - the "estimate" retail source. Germany additionally requires a
# postal code for the grid-fee lookup; the other markets use country-wide
# default assumptions. The own formula works in every market.
RETAIL_MARKETS: Final[frozenset[str]] = frozenset(
    {"DE", "NL", "DK1", "DK2", "AT", "NO1", "NO2", "NO3", "NO4", "NO5"}
)

# Markets whose prices the API can convert from euro into their own
# currency, at the ECB's daily reference rate. Only markets the API answers
# in euro by default are listed: Denmark and Norway get DKK and NOK without
# asking, so the option has nothing to change there - and must not change
# anything, because the currency becomes part of a stored plan's key, and a
# key that changed on upgrade would re-pick a plan halfway through its block.
LOCAL_CURRENCY_BY_MARKET: Final[dict[str, str]] = {
    "CZ": "CZK",
    "PL": "PLN",
    "SE1": "SEK",
    "SE2": "SEK",
    "SE3": "SEK",
    "SE4": "SEK",
}

MARKETS: Final[dict[str, str]] = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "CH": "Switzerland",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK1": "Denmark DK1",
    "DK2": "Denmark DK2",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GR": "Greece",
    "ITN": "Italy North (ITN)",
    "IT_CNOR": "Italy Centre-North (IT_CNOR)",
    "IT_CSUD": "Italy Centre-South (IT_CSUD)",
    "IT_SUD": "Italy South (IT_SUD)",
    "IT_CALA": "Italy Calabria (IT_CALA)",
    "IT_SICI": "Italy Sicily (IT_SICI)",
    "IT_SARD": "Italy Sardinia (IT_SARD)",
    "NL": "Netherlands",
    "NO1": "Norway NO1",
    "NO2": "Norway NO2",
    "NO3": "Norway NO3",
    "NO4": "Norway NO4",
    "NO5": "Norway NO5",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE1": "Sweden SE1",
    "SE2": "Sweden SE2",
    "SE3": "Sweden SE3",
    "SE4": "Sweden SE4",
    "SK": "Slovakia",
}
