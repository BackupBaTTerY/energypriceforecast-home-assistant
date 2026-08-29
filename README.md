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
- Optional "cheapest hours" plan - the N cheapest individual hours of each
  fixed, repeating block (e.g. every calendar day, or every 48 hours), which
  may be non-contiguous, unlike the single best continuous window above. Once
  a block's plan is picked it is locked and never reshuffled by a later
  forecast update, and no plan is published at all for a block the forecast
  does not yet fully cover
- Optional independent weekend plan (Saturday 00:00 to Monday 00:00) for
  loads that are only flexible on weekends, e.g. EV charging
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

## Entities

One device is created per configured market.

### Finding your entity IDs first

**Do not copy entity IDs out of this README - look up your own.** Open
**Settings > Devices & services > Energy Price Forecast EU**, click the device,
and use the entity list there; or filter for `energypriceforecast` under
**Developer tools > States**.

The names in the tables below are the English display names. Your `entity_id`
will usually *not* match them, for two reasons that surprise most people:

- **They follow your Home Assistant language.** On a German instance the
  "Current price" sensor is `sensor..._aktueller_strompreis`, and the price
  series is `sensor..._preisreihe`, not `_price_series`. Searching for the
  English name finds nothing.
- **They are generated once, when each entity is first created**, and are never
  renamed afterwards. Entities that an update adds later can therefore pick up
  a different prefix than the ones created earlier - for example an area name,
  if the device was moved into an area in the meantime. Mixed prefixes within
  one device are normal and harmless.

If the inconsistency bothers you, rename them: click an entity, then the gear
icon, and edit its entity ID. That is a purely local change and nothing in this
integration depends on the ID.

### Always created

| Name | Type | Unit |
| --- | --- | --- |
| Current price | sensor | market currency, e.g. EUR/kWh |
| Current CO2 intensity | sensor | gCO2/kWh |
| Cheapest window average price | sensor | market currency |
| Cheapest window start | sensor | timestamp |
| Cheapest window end | sensor | timestamp |
| Greenest window average CO2 | sensor | gCO2/kWh |
| Greenest window start | sensor | timestamp |
| Greenest window end | sensor | timestamp |
| Combined window score | sensor | - |
| Price forecast series | sensor | market currency |
| Cheapest window active | binary sensor | on/off |
| Greenest window active | binary sensor | on/off |

Plus four diagnostic entities, shown separately in the device page: **Allowed
horizon** and **Used horizon** (hours), **API-key status**, and **Last API
update** (timestamp).

"Cheapest window" here is the single *contiguous* window of the configured
best-window duration - not the same thing as the cheapest-hours plan below.

### With retail pricing enabled

| Name | Type | Unit |
| --- | --- | --- |
| Current retail price | sensor | market currency |
| Cheapest window average retail price | sensor | market currency |
| Cheapest window start (retail) | sensor | timestamp |
| Cheapest window end (retail) | sensor | timestamp |
| Cheapest window active (retail) | binary sensor | on/off |

These mirror the base entities but are computed on the all-in retail price, so
the cheapest window can differ from the spot-price one.

### With a cheapest-hours count above 0

| Name | Type | Unit |
| --- | --- | --- |
| Next cheapest hour | sensor | timestamp |
| Cheapest hours active | binary sensor | on/off |

### With a weekend-hours count above 0

| Name | Type | Unit |
| --- | --- | --- |
| Next weekend cheapest hour | sensor | timestamp |
| Weekend cheapest hours active | binary sensor | on/off |

### Attributes worth knowing

- **Price forecast series** and **Current retail price** carry `raw_today`,
  `raw_tomorrow` and `raw_forecast` - lists of `{start, end, value}` slots for
  charting (see the chart section below).
- **Next cheapest hour** and **Next weekend cheapest hour** carry `hours`: the
  full locked plan as a list of `{start, end, average_value}`.

**To inspect them, use Developer tools > States**, pick the entity, and read
the attributes in the panel on the right. They will not show up in the history
or in the database (see below), so looking there is a dead end - you do not
need a throwaway automation that logs them.

These attributes are deliberately excluded from the recorder database since
0.7.2. They hold one entry per 15-minute slot across the whole horizon, which
exceeds the recorder's 16 KB per-state limit, so it used to drop them and log a
warning on every update. They are always available live for charts, templates
and automations - only their *history* is not stored.

**No `recorder:` configuration is needed for this, and adding one is a bad
idea.** Excluding the whole entity (`recorder: exclude: entities:`) would also
discard its state, so you would lose the price history itself - a much bigger
loss than the attributes. If you are still seeing "State attributes ... exceed
maximum size" warnings, you are on a version older than 0.7.2; update instead
of configuring around it.

## Horizon

The public access currently provides up to 48 hours. An eligible API key can
raise the horizon up to 120 hours. The integration exposes both the requested
and the actually permitted horizon, so automations can detect the effective
range.

## Which plan should I use?

### Heat pump or water heater: N cheapest hours per X-hour block

Time is split into fixed, back-to-back blocks, and within each block the
integration picks the cheapest individual hours. Once a block's plan is
picked, it does not move: it is never reshuffled by a later forecast update.

Examples:

- **12 of 24 hours, start 00:00**: every calendar day, the twelve cheapest
  individual hours are picked. A reasonable starting point for a heat pump -
  the actual count should match the house's heat demand.
- **24 of 48 hours, anchored Monday 00:00**: 24 hours are picked per fixed
  two-day block. Blocks run back-to-back, e.g. Monday-to-Wednesday,
  Wednesday-to-Friday, Friday-to-Sunday.

A longer block gives the integration more hours to choose from, which
typically means more savings - but for a heating system it can also spread
runtime less evenly, so room temperature and hot-water comfort may swing more.
Choose only as much flexibility as the house and its occupants can tolerate.

`N` (cheapest hours) must never be greater than `X` (block length) - the
config flow rejects that combination. If the forecast does not yet cover the
full block, no partial plan is published; the plan appears once the forecast
catches up.

### EV charging on weekends

The weekend plan is independent of the block-based plan above and always ends
Monday at 00:00. With public access it uses the fixed 48-hour window from
Saturday 00:00 to Monday 00:00. A plan is only published once the entire
window is covered by the forecast (including hours already in the past), so a
plan built after a late restart is never partial.

### Two similarly-named features

- **Cheapest hours plan**: several individual cheap hours inside a fixed
  block - for flexible loads that can run in interrupted bursts.
- **Cheapest window**: a single, uninterrupted window of the configured
  "best-window duration" - for a device that needs to run for several hours
  without a pause.

## Data updates

All entities of one market share one API request per poll (default every 30
minutes, configurable from 15 to 120 minutes). The integration does not
create one request per entity.

## Switching a device with the plan

Charting the price is the visible half; the point of the plan is switching
something. Each plan has a binary sensor that is simply **on** while the
current hour is one the plan picked, which is all an automation needs.

Add these under **Settings > Automations & scenes > Create automation > Edit in
YAML**, and replace the entity IDs with your own (see
[Finding your entity IDs](#finding-your-entity-ids-first)).

Heat pump on during the planned cheap hours, off outside them:

```yaml
alias: Heat pump during cheap hours
triggers:
  - trigger: state
    entity_id: binary_sensor.CHANGE_ME_cheapest_hours_active
    to: "on"
    id: cheap_on
  - trigger: state
    entity_id: binary_sensor.CHANGE_ME_cheapest_hours_active
    to: "off"
    id: cheap_off
actions:
  - choose:
      - conditions:
          - condition: trigger
            id: cheap_on
        sequence:
          - action: switch.turn_on
            target:
              entity_id: switch.CHANGE_ME_heat_pump
      - conditions:
          - condition: trigger
            id: cheap_off
        sequence:
          - action: switch.turn_off
            target:
              entity_id: switch.CHANGE_ME_heat_pump
mode: queued
```

For an EV charger on the weekend plan, use
`binary_sensor.CHANGE_ME_weekend_cheapest_hours_active` instead.

Two things worth knowing before you rely on this:

- **The plan can be absent.** If the forecast does not yet cover the whole
  block, no plan is published and the binary sensor is `unavailable`, not
  `off`. An automation triggered on `to: "off"` will not fire on that
  transition, so the device keeps its last state. If that matters for your
  load, add a fallback - for example a time-based condition, or treat
  `unavailable` explicitly.
- **A restart re-evaluates, it does not re-plan.** The plan itself is stored
  and survives restarts, so a reload will not move your cheap hours. But the
  binary sensor only reflects the current hour, so if Home Assistant is down
  when a planned hour starts, that transition is simply missed.

If you want a device to run for several hours *without interruption* instead,
use the cheapest-window entities rather than the plan - see
[Two similarly-named features](#two-similarly-named-features).

## Build a price chart

Two sensors carry `raw_today` / `raw_tomorrow` attributes - a list of price
time slots, made for charting with the community card
[apexcharts-card](https://github.com/RomRider/apexcharts-card) (installed
separately via HACS): the always-on price series sensor (day-ahead/spot price)
and, if you enabled retail pricing during setup, the current retail price
sensor (assumption-based all-in price). Both also carry a third attribute,
`raw_forecast`: the entries beyond the published day-ahead window - the actual
ML/weather-based forecast, richer with a longer configured horizon.

**If you enabled retail pricing, chart the retail sensor.** The spot price is
not what you pay, so a chart built on it will disagree with your bill. The
shape of both sensors' attributes is identical, so the card below works for
either - just point it at the right entity.

### Ready-to-paste card

Replace both `sensor.CHANGE_ME` lines with your own entity (see
[Finding your entity IDs](#finding-your-entity-ids-first) - on a German
instance it is likely `sensor..._preisreihe` or `sensor..._aktueller_endkundenpreis`).
Add the card via **Dashboard > Edit > Add card > Manual**.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Electricity price
graph_span: 4d
span:
  start: day
now:
  show: true
  label: Now
yaxis:
  - id: price
    decimals: 3
    apex_config:
      title:
        text: EUR/kWh
series:
  # Both sensor.CHANGE_ME lines must be the SAME entity.
  # Retail pricing enabled? Use your retail price sensor here, not the price
  # series sensor - the spot price is not what you pay, and a chart on it
  # will disagree with the retail price shown elsewhere on your dashboard.
  - entity: sensor.CHANGE_ME
    name: Known (day-ahead)
    yaxis_id: price
    type: line
    curve: stepline
    color: "#43a047"
    extend_to: false
    stroke_width: 2
    data_generator: |
      const known = [...(entity.attributes.raw_today ?? []),
                     ...(entity.attributes.raw_tomorrow ?? [])];
      return known.map(e => [new Date(e.start).getTime(), e.value]);
  - entity: sensor.CHANGE_ME
    name: Forecast
    yaxis_id: price
    type: line
    curve: stepline
    color: "#fb8c00"
    extend_to: false
    stroke_width: 2
    data_generator: |
      const known = [...(entity.attributes.raw_today ?? []),
                     ...(entity.attributes.raw_tomorrow ?? [])];
      const forecast = entity.attributes.raw_forecast ?? [];
      // Start the forecast at the last known point, otherwise the two lines
      // are drawn with a gap and the forecast looks like it disagrees with
      // the last known price instead of continuing from it.
      const join = known.length ? [known[known.length - 1]] : [];
      return [...join, ...forecast].map(
        e => [new Date(e.start).getTime(), e.value]);
```

Both series are `extend_to: false` on purpose: without it, apexcharts-card
stretches the last value to the edge of the graph, inventing prices that were
never forecast.

### Showing the planned cheap hours in the chart

If you configured a cheapest-hours count, add this series to mark the hours the
plan actually picked, so you can see at a glance whether the plan lines up with
the price valleys. Point it at your *Next cheapest hour* sensor (German:
`sensor..._naechste_guenstige_stunde`), and use the *Next weekend cheapest
hour* sensor for the weekend plan.

Add it as the **first** entry under `series:`, before the two price series -
apexcharts draws series in order, so listing it first keeps the bands behind
the price lines instead of on top of them.

```yaml
  - entity: sensor.CHANGE_ME_cheapest_hours
    name: Planned hours
    yaxis_id: plan
    type: area
    curve: stepline
    color: "#1e88e5"
    opacity: 0.22
    stroke_width: 0
    extend_to: false
    show:
      legend_value: false
    data_generator: |
      const raw = (entity.attributes.hours ?? []).map(h => ({
        start: new Date(h.start).getTime(),
        end: new Date(h.end).getTime(),
      })).sort((a, b) => a.start - b.start);
      // Merge back-to-back hours into one band, otherwise every boundary
      // between two adjacent planned hours shows up as a zero-width dip.
      const runs = [];
      for (const hour of raw) {
        const last = runs[runs.length - 1];
        if (last && hour.start <= last.end) last.end = Math.max(last.end, hour.end);
        else runs.push({ ...hour });
      }
      return runs.flatMap(run => [[run.start, 1], [run.end, 0]]);
```

It needs a second, hidden axis so the bands do not distort the price scale.
Add this under the existing `yaxis:` list - `max: 1` against a plotted value of
1 makes each band span the full height of the chart:

```yaml
  - id: plan
    show: false
    min: 0
    max: 1
```

A stepped area is used rather than columns on purpose. A column series plots
one point per planned hour and lets the card derive the bar width from the
spacing between points, which makes scattered hours render as hairlines over a
multi-day span. Emitting a start and an end point per hour instead draws a band
of the hour's real width, at any zoom level, and a run of consecutive planned
hours becomes one wide band rather than several touching bars.

### Or let an AI build it for you

Paste the prompt below into your AI assistant of choice to get a card tailored
to your setup - useful if you want a different layout, only today's prices, or
CO2 alongside the price.

<details>
<summary>Show the copyable prompt</summary>

```
Help me build a Home Assistant Lovelace card that charts electricity prices from the Energy Price Forecast EU integration using the apexcharts-card custom card.

The integration creates a sensor whose entity_id ends in "_price_series" (day-ahead/spot price, the exact name depends on my chosen market, for example sensor.energy_price_forecast_eu_de_price_forecast_series) and, if I enabled retail pricing, a second sensor ending in "_retail_current_price" (assumption-based all-in price) with the same attribute shape. Each sensor's state is its current price; its attributes raw_today, raw_tomorrow and raw_forecast are each a list of objects shaped like {"start": ISO8601 timestamp, "end": ISO8601 timestamp, "value": number}. raw_today/raw_tomorrow only ever cover the published day-ahead window (known prices, never estimated); raw_forecast holds only the entries beyond that window - the actual ML/weather-based forecast. The value's unit matches the market's currency (for example EUR/kWh).

Entity IDs follow the Home Assistant language, so do not guess them from the English names above - on a German instance the price series is sensor..._preisreihe and the retail price is sensor..._aktueller_endkundenpreis. Ask me for the exact ID rather than assuming one.

My actual entity_id is: <PASTE YOUR ENTITY ID HERE - find it under Settings > Devices & Services > Energy Price Forecast EU, or Developer Tools > States, filtering for "energypriceforecast">

Before writing YAML, ask me:
1. Do I already have HACS and the apexcharts-card custom card installed? If not, tell me to install apexcharts-card via HACS first (category: Frontend/Plugin).
2. Did I enable retail pricing? If I did, chart the retail all-in price rather than the spot price unless I explicitly say otherwise - the spot price is not what I pay, so a chart built on it will disagree with my bill. Ask me which entity that is instead of guessing the ID.
3. Should the chart show today only, today and tomorrow together, or the known prices plus the forecast (raw_today + raw_tomorrow + raw_forecast) as two visually distinct series (e.g. solid vs dashed, different colors)?
4. Did I configure a cheapest-hours count or a weekend plan? If so, offer to mark the planned hours in the chart, using the "hours" attribute of the corresponding sensor (a list of {"start", "end", "average_value"}). Seeing the plan against the price curve is usually the point of the chart.
5. Do I want a bar chart per hour or a line/area chart?
6. Do I want to use this to actually switch a device (heat pump, water heater, car charger), rather than only look at it? If so, say that a chart alone will not do that, and offer to write a matching automation triggered on the binary sensor that is on during the planned hours.

Rules for your result:
- Use only the raw_today / raw_tomorrow / raw_forecast attributes I described. Do not invent other attributes or a different data shape.
- Use apexcharts-card's data_generator to turn the attribute list into a chart series - do not assume the card accepts the attribute directly as a series.
- If I asked for known prices and forecast as separate series, use two series against the same entity (one summing raw_today+raw_tomorrow, one for raw_forecast), each with its own data_generator, and set extend_to: false on both - otherwise apexcharts-card visually extends the last value to the edge of the graph, which is misleading here.
- The forecast series must start where the known series ends. In the raw_forecast data_generator, prepend the last entry of raw_today+raw_tomorrow to the forecast points. The two attributes are adjacent but not overlapping, so without that point the two lines are drawn with a visible gap, which reads as the forecast disagreeing with the last known price instead of continuing from it. Keep the prepended point in the forecast series' own colour and style, so no known value is presented as a forecast or the other way round.
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
