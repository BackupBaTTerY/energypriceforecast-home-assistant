# Energy Price Forecast EU for Home Assistant

Custom Home Assistant integration for electricity-price and consumption-based
CO2 forecasts from [Energy Price Forecast EU](https://energypriceforecast.eu/).

The integration combines published day-ahead prices with forecast values for
the remaining horizon. It exposes ready-to-use entities for automations without
requiring YAML or JSON templates.

> Beta: entity names and details may still change before version 1.0.

## Features

- UI-based setup through Home Assistant's integration flow
- Current electricity price and current CO2 intensity
- Cheapest price window and greenest CO2 window
- Binary sensors indicating whether a best window is active now
- Combined price/CO2 window score
- Access and horizon diagnostics
- Multiple market entries, for example DK1 and DK2
- Optional API key support

## Supported markets

AT, BE, CZ, DE, DK1, DK2, FI, FR, NL, NO1-NO5, PL and SE1-SE4.

## Installation with HACS

Until the integration is part of the HACS default repository list:

1. Open HACS in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/hendriksoppa-droid/energypriceforecast-home-assistant`
   as category **Integration**.
4. Install **Energy Price Forecast EU** and restart Home Assistant.
5. Open **Settings > Devices & services > Add integration** and search for
   **Energy Price Forecast EU**.

## Manual beta installation

1. Copy `custom_components/energypriceforecast` from this repository to
   `/config/custom_components/energypriceforecast` in Home Assistant.
2. Restart Home Assistant.
3. Open **Settings > Devices & services > Add integration** and search for
   **Energy Price Forecast EU**.

Remove an older YAML package using the same data only after the new entities
have been checked. Both approaches can run in parallel during the beta test.

## Horizon

The public access currently provides up to 48 hours. An eligible API key can
raise the horizon to 120 hours. The integration exposes both the requested and
the actually permitted horizon, so automations can detect the effective range.

## Data updates

All entities of one market share one API request every 30 minutes. The
integration does not create one request per entity.

## Privacy

The integration sends the selected market, horizon and window duration to the
public API. If configured, the API key is sent as a bearer token. It is stored
inside the Home Assistant config entry and is redacted from diagnostics.

## Support

- [Setup documentation](https://energypriceforecast.eu/en/home-assistant-electricity-price-co2-forecast/)
- [Issue tracker](https://github.com/hendriksoppa-droid/energypriceforecast-home-assistant/issues)
- [Home Assistant community topic](https://community.home-assistant.io/t/free-electricity-price-and-co2-forecast-api-for-home-assistant-automations/1014796)
