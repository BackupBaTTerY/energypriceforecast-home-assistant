"""Constants for Energy Price Forecast EU."""

from typing import Final

DOMAIN: Final = "energypriceforecast"
NAME: Final = "Energy Price Forecast EU"
DEFAULT_API_URL: Final = (
    "https://api.energypriceforecast.eu/api/v1/home-assistant/summary"
)
PRICES_API_URL: Final = (
    "https://api.energypriceforecast.eu/api/v1/home-assistant/prices"
)
DEFAULT_HORIZON_HOURS: Final = 48
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
MAX_CHEAPEST_HOURS_WINDOW_HOURS: Final = 168
CONF_CHEAPEST_HOURS_START_HOUR: Final = "cheapest_hours_start_hour"
DEFAULT_CHEAPEST_HOURS_START_HOUR: Final = 0

# The weekend plan: a separate, fixed Saturday 00:00 - Monday 00:00 block for
# loads that are specifically flexible on weekends (e.g. EV charging), picked
# and locked the same way as the regular cheapest-hours block.
CONF_WEEKEND_HOURS_COUNT: Final = "weekend_hours_count"
DEFAULT_WEEKEND_HOURS_COUNT: Final = 0
MAX_WEEKEND_HOURS_COUNT: Final = 48

PLATFORMS: Final = ["sensor", "binary_sensor"]

# Markets where the API can compute an assumption-based retail (all-in)
# price. Germany additionally requires a postal code for the grid-fee
# lookup; the other markets use country-wide default assumptions.
RETAIL_MARKETS: Final[frozenset[str]] = frozenset(
    {"DE", "NL", "DK1", "DK2", "AT", "NO1", "NO2", "NO3", "NO4", "NO5"}
)

MARKETS: Final[dict[str, str]] = {
    "AT": "Austria",
    "BE": "Belgium",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK1": "Denmark DK1",
    "DK2": "Denmark DK2",
    "FI": "Finland",
    "FR": "France",
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
