"""Constants for Energy Price Forecast EU."""

from typing import Final

DOMAIN: Final = "energypriceforecast"
NAME: Final = "Energy Price Forecast EU"

# Sent as the User-Agent, so the API can tell which version a request came
# from. Must equal manifest.json's version - test_version.py enforces that,
# because this drifted to 0.1.0 for ten releases and every request from
# every user was mislabelled the whole time.
VERSION: Final = "1.4.0"
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
CONF_RETAIL_PRICING: Final = "retail_pricing"
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

PLATFORMS: Final = ["sensor", "binary_sensor"]

# Markets where the API can compute an assumption-based retail (all-in)
# price. Germany additionally requires a postal code for the grid-fee
# lookup; the other markets use country-wide default assumptions.
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
    "CH": "Switzerland",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK1": "Denmark DK1",
    "DK2": "Denmark DK2",
    "FI": "Finland",
    "FR": "France",
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
    "SE1": "Sweden SE1",
    "SE2": "Sweden SE2",
    "SE3": "Sweden SE3",
    "SE4": "Sweden SE4",
}
