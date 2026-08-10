# Energy Price Forecast EU for Home Assistant

Custom Home Assistant integration for electricity-price and consumption-based
CO2 forecasts from [Energy Price Forecast EU](https://energypriceforecast.eu/).

The integration combines published day-ahead prices with forecast values for
the remaining horizon. It exposes ready-to-use entities for automations without
requiring YAML or JSON templates.

## Features

- UI-based setup through Home Assistant's integration flow
- Current electricity price and current CO2 intensity
- Cheapest price window and greenest CO2 window
- Binary sensors indicating whether a best window is active now
- Combined price/CO2 window score
- Raw price-series sensor with `raw_today` / `raw_tomorrow` attributes
  (compatible with `apexcharts-card` and custom templates)
- Optional "cheapest N hours" tracking - the N cheapest upcoming hours, which
  may be non-contiguous, unlike the single best continuous window above
- Optional assumption-based all-in retail price for supported markets
- Configurable poll interval (15-120 minutes)
- Access and horizon diagnostics, with API key and postal code redacted
- Multiple market entries, for example DK1 and DK2
- Optional API key support

## Supported markets

AT, BE, CZ, DE, DK1, DK2, FI, FR, NL, NO1-NO5, PL and SE1-SE4.

## Installation with HACS

Until the integration is part of the HACS default repository list:

1. Open HACS in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/BackupBaTTerY/energypriceforecast-home-assistant`
   as category **Integration**.
4. Install **Energy Price Forecast EU** and restart Home Assistant.
5. Open **Settings > Devices & services > Add integration** and search for
   **Energy Price Forecast EU**.

## Manual installation

1. Copy `custom_components/energypriceforecast` from this repository to
   `/config/custom_components/energypriceforecast` in Home Assistant.
2. Restart Home Assistant.
3. Open **Settings > Devices & services > Add integration** and search for
   **Energy Price Forecast EU**.

An older YAML package using the same API remains fully supported and can run
in parallel - remove it only after the new entities have been checked.

## Horizon

The public access currently provides up to 48 hours. An eligible API key can
raise the horizon to 120 hours. The integration exposes both the requested and
the actually permitted horizon, so automations can detect the effective range.

## Data updates

All entities of one market share one API request per poll (default every 30
minutes, configurable from 15 to 120 minutes). The integration does not
create one request per entity.

## Build a price chart with AI

Two sensors carry `raw_today` / `raw_tomorrow` attributes - a list of price
time slots, made for charting with the community card
[apexcharts-card](https://github.com/RomRider/apexcharts-card) (installed
separately via HACS): the always-on sensor ending in `_price_series`
(day-ahead/spot price) and, if you enabled retail pricing during setup, the
sensor ending in `_retail_current_price` (assumption-based all-in price).
Both also carry a third attribute, `raw_forecast`: the entries beyond the
published day-ahead window - the actual ML/weather-based forecast, richer
with a longer configured horizon. Paste the prompt below into your AI
assistant of choice to get a ready-to-use Lovelace card for your actual
entity_id.

<details>
<summary>Show the copyable prompt</summary>

```
Help me build a Home Assistant Lovelace card that charts electricity prices from the Energy Price Forecast EU integration using the apexcharts-card custom card.

The integration creates a sensor whose entity_id ends in "_price_series" (day-ahead/spot price, the exact name depends on my chosen market, for example sensor.energy_price_forecast_eu_de_price_forecast_series) and, if I enabled retail pricing, a second sensor ending in "_retail_current_price" (assumption-based all-in price) with the same attribute shape. Each sensor's state is its current price; its attributes raw_today, raw_tomorrow and raw_forecast are each a list of objects shaped like {"start": ISO8601 timestamp, "end": ISO8601 timestamp, "value": number}. raw_today/raw_tomorrow only ever cover the published day-ahead window (known prices, never estimated); raw_forecast holds only the entries beyond that window - the actual ML/weather-based forecast. The value's unit matches the market's currency (for example EUR/kWh).

My actual entity_id is: <PASTE YOUR ENTITY ID HERE - find it under Settings > Devices & Services > Energy Price Forecast EU, or Developer Tools > States, filtering for "price_series" or "retail_current_price">

Before writing YAML, ask me:
1. Do I already have HACS and the apexcharts-card custom card installed? If not, tell me to install apexcharts-card via HACS first (category: Frontend/Plugin).
2. Do I want to chart the spot/day-ahead price (_price_series) or my retail all-in price (_retail_current_price), if I have that enabled?
3. Should the chart show today only, today and tomorrow together, or the known prices plus the forecast (raw_today + raw_tomorrow + raw_forecast) as two visually distinct series (e.g. solid vs dashed, different colors)?
4. Do I also want the cheapest-hours window highlighted, if I enabled that feature? (binary_sensor ...is_in_cheapest_hours / sensor ...cheapest_hours_next_start)
5. Do I want a bar chart per hour or a line/area chart?

Rules for your result:
- Use only the raw_today / raw_tomorrow / raw_forecast attributes I described. Do not invent other attributes or a different data shape.
- Use apexcharts-card's data_generator to turn the attribute list into a chart series - do not assume the card accepts the attribute directly as a series.
- If I asked for known prices and forecast as separate series, use two series against the same entity (one summing raw_today+raw_tomorrow, one for raw_forecast), each with its own data_generator, and set extend_to: false on both - otherwise apexcharts-card visually extends the last value to the edge of the graph, which is misleading here.
- Quote any string value (title, name, tooltip format) that itself contains a colon, like "Known: forecast" or "dd.MM. HH:mm" - an unquoted colon inside a YAML value breaks parsing.
- Produce a complete, correctly indented YAML block for a manual Lovelace card (type: custom:apexcharts-card).
- Tell me exactly where to paste it (Dashboard > Edit > Add card > Manual).
- If information is missing, ask - do not guess my entity_id or market.
```

</details>

## Privacy

The integration sends the selected market, horizon and window duration to the
public API. If configured, the API key is sent as a bearer token. It is stored
inside the Home Assistant config entry and is redacted from diagnostics.

## Support

- [Setup documentation](https://energypriceforecast.eu/en/home-assistant-electricity-price-co2-forecast/)
- [Issue tracker](https://github.com/BackupBaTTerY/energypriceforecast-home-assistant/issues)
- [Home Assistant community topic](https://community.home-assistant.io/t/free-electricity-price-and-co2-forecast-api-for-home-assistant-automations/1014796)
