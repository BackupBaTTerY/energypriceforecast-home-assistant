# Entity details

What the combined window and score are made of, where the CO2 number comes
from, and the attributes each entity carries. The list of entities is in
the [README](../README.md#entities).

## The combined window and score

### The combined window *(1.2.0)*

The cheapest window and the greenest window are rarely the same hours. The
**combined window** is the compromise: the API searches the horizon for the
window that scores best on price *and* CO2 intensity together, weighting the
two equally. **Combined window start** and **end** say when it is, and
**Combined window active** switches while it runs.

### Combined score now *(1.5.0)*

**Combined score now** answers a different question, and the one an
automation can act on: how good is *this moment*, compared with the rest of
the coming day, on price and CO2 together? It runs from 0 to 100, higher is
better, and it reads as a share: **80 means the present stands better than
80% of the hours ahead**. 100 is the best slot ahead, 0 the worst. It moves on
at every quarter hour, not only when the integration polls.

How it is worked out:

1. **The reference** is the next 24 hours - but only as far as prices are
   published. Tomorrow's day-ahead prices appear around 13:00, so from then on
   the reference is a full day; just before the auction it ends at midnight.
   Forecast prices stay out on purpose: over 30 days the frozen forecast for
   tomorrow's early hours ran 1.5 to 4.3 ct/kWh too low in 11 of 12 markets,
   which would have made every morning look worse than it was.
2. **A yardstick for each quantity.** For price and for CO2 separately: the
   median of the reference, and its typical spread - the distance from the
   10th to the 90th percentile, so a single spike cannot set the scale. The
   spread never counts as less than 25 gCO2/kWh or 10% of the average price,
   so a quantity that barely moves barely counts.
3. **A standing for every slot:** how many typical spreads its price lies
   below the median, plus the same for its CO2 intensity.
4. **The score** is the share of the other reference slots whose standing is
   worse than the present's, ties counting half.

An example with German numbers from September: at 14:00 the price was 16.1
ct - almost exactly the median of 15.2 ct, a price part of -0.03 - but the grid
ran at 167 g against a median of 427 g, a CO2 part of +0.83. Of the 23 other
hours, 19 stood worse, so the score was 19 / 23 = **83**. Clean midday power
earned it, not cheap power.

The attributes show the workings: `price_part` and `co2_part` for the present;
`co2_share_percent`, how much of today's ranking comes from CO2 at all;
`price_spread` and `co2_spread_g_kwh`; and `reference_start`, `reference_end`
and `reference_hours`.

Since 1.10.0, `score_series` carries the current and every upcoming slot in
that same reference as `{start, end, value}`. All points use the same medians,
spreads and competing slots, so an 80 later in the line means the same thing
as an 80 now. The first point is therefore exactly the sensor's current state.
The series deliberately ends with the published prices: price forecasts remain
excluded for the measured bias described above. It is live chart/planning data
and is excluded from Home Assistant's recorder. It is an outlook rather than a
locked plan: values can move when new data arrives and as the shared reference
rolls forward. Loads that require a guaranteed number of daily runtime slots
still need one of the locked plans.

This ApexCharts card draws the recorded score for the previous six hours and
the current outlook on the same 0-100 axis. Replace `sensor.CHANGE_ME` twice
with your **Combined score now** entity:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Combined price / CO2 score
graph_span: 30h
span:
  start: hour
  offset: -6h
now:
  show: true
  label: Now
yaxis:
  - min: 0
    max: 100
    decimals: 0
series:
  - entity: sensor.CHANGE_ME
    name: Recorded
    type: line
    curve: stepline
    color: "#78909c"
    extend_to: false
    group_by:
      duration: 15min
      func: last
  - entity: sensor.CHANGE_ME
    name: Outlook
    type: line
    curve: stepline
    color: "#43a047"
    extend_to: false
    stroke_width: 2
    data_generator: |
      return (entity.attributes.score_series ?? [])
        .map(e => [new Date(e.start).getTime(), e.value])
        .sort((a, b) => a[0] - b[0]);
```

**Use it for loads that can wait**, with a threshold: "start the dishwasher
when the score reaches 80" runs it among the better hours of what is still
ahead. **For loads that must run every day, use a plan instead.** The
cheapest- and cleanest-hours plans pick within a fixed daily block and never
skip a day, while a score can keep a load waiting when the hours ahead are
better.

What it does not tell you:

- **It does not compare regions.** It ranks hours within your own market only.
  On the same September day the worst-rated hour in Norway (NO3) ran at 20 g,
  eight times cleaner than the best-rated hour in Germany at 167 g. Compare
  regions with **Current price** and **Current CO2 intensity**.
- **"Combined" mixes differently by market.** In a hydro grid CO2 sits at
  20-21 g all day, the floor makes that difference count for nothing, and the
  score becomes a price score - `co2_share_percent` shows 0. That is right,
  since there is no CO2 to save by shifting there, but it is worth knowing.
- **It says nothing about how much you save.** Between the best and the worst
  hour lay 18 ct in Sweden (SE3) and 54 ct in Germany on the same day. The
  spreads in the attributes say which kind of day it is.
- **CO2 is always a forecast.** Future emissions cannot be measured, so the CO2
  half of the ranking rests on the model even where the prices are published.
- With no published price for the present, or fewer than 4 published hours
  ahead, the sensor is **unavailable** rather than ranking against too little.

### Combined window score (deprecated)

Until 1.5.0 the integration offered **Combined window score** instead. It rated
the combined window, and in a solar market the cheapest and the cleanest
windows are nearly always the same midday hours - so it sat at 100 and said
little more than that the two agree. New installs no longer get it. Existing
installs keep it, still working and with its `raw_score` attribute, so no
automation breaks; it will be removed in 2.0. Move automations to **Combined
score now**, or to **Combined window active** if they act on the window
itself.

## Where the CO2 number comes from

It is **modelled, not measured**, and it is a forecast in the same sense the
price is: the generation mix is predicted from weather and demand, and each
ENTSO-E production type is multiplied by a lifecycle emission factor to give
gCO2e/kWh.

Four things are worth knowing before an automation acts on it:

- **ENTSO-E categories are coarse.** They say nothing about plant age, fuel
  quality, efficiency or lifecycle system boundary, and one category can cover
  very different technologies.
- **Unknown generation gets a placeholder**, deliberately a high one, so that
  what we cannot identify is not silently counted as clean. That value is a
  guard, not a measurement.
- **Storage is approximated in both directions.** Pumped storage is carried
  as hydro and battery storage as unidentified generation, where the honest
  answer would be the intensity of the electricity each was charged with plus
  its losses. While storage is a small share of generation this barely moves
  the curve; it will be revisited when that stops being true.
- **The literature ranges are wide** - UNECE puts photovoltaics at roughly
  8-83 and wind at 7.8-23 gCO2e/kWh - so a single factor per category is
  always a choice, not a fact.

The factors themselves are not restated here - a copy would drift from the
values actually used, and an assumption that disagrees with the computation is
worse than none. They belong in the API's `co2.assumptions` field instead: the
versioned profile name, the value per production type, and for each one whether
it is a published IPCC median or a project stand-in, which you cannot tell by
looking at the number.

The **CO2 forecast series** sensor carries the same block in its `assumptions`
attribute *(1.2.1)*, so you can read it in Home Assistant without calling the
API yourself. A market whose model has not published its factors yet reports
`available: false` there and says why, rather than showing figures that might
not be the ones your reading was computed with.

None of this makes the number useless: for deciding *which hour today is
cleaner than another*, the mix forecast carries the signal and the factor
uncertainty largely cancels out. It does mean the absolute gCO2e/kWh should
not be treated as a measured quantity, and that "grams avoided" is a
comparison against another hour in the same block, nothing more.

## Attributes worth knowing

- **Price forecast series** and **Current retail price** *(improved in 1.9.0)*
  carry `raw_today`,
  `raw_tomorrow` and `raw_forecast` - lists of `{start, end, value}` slots for
  charting (see the chart section below). The integration retains today's
  published slots after it has seen them as the API's rolling window moves
  forward, including across a Home Assistant restart. During normal operation,
  `raw_today` therefore keeps the line visible from local midnight through the
  chart's Now marker. The cache starts fresh at local midnight. `raw_today` and
  `raw_tomorrow` hold published day-ahead prices only, so `raw_tomorrow` is
  **empty until
  tomorrow's prices are published** (usually early afternoon) - the estimate
  for those hours is in `raw_forecast` instead, and the two never overlap.
  Both sensors select the current slot at every quarter hour, independently of
  the configured API poll interval and without making an extra request.
- Both price sensors also carry `raw_forecast_reference` *(1.12.0)*: what was
  predicted for each future slot, as `{start, end, value, captured_at,
  final_value, final_captured_at}` *(1.13.0)*. `value` is the **first forecast
  received** - how far ahead that was depends on your horizon. `final_value` is
  the **last forecast before the official price arrived**, which is the moment
  our published quality figures measure, so a chart drawn on it can be held
  against them; the ready-to-paste card below uses it. While a slot is still a
  forecast, `final_value` follows every update; the first official price settles
  it, and a forecast the API falls back to later cannot overwrite it. This
  comparison is stored locally and survives restarts. It keeps today's and
  upcoming slots;
  yesterday's are removed on the next successful update after market midnight.
  `captured_at` is when Home Assistant received the value, not the model's
  creation time. A slot first seen with an official price, or after it started,
  cannot be retroactively given a forecast. No extra API request is needed.
- **CO2 forecast series** carries the same `raw_today` / `raw_tomorrow` /
  `raw_forecast` attributes as the price series, in gCO2/kWh, so a chart card
  built for prices works by swapping the entity. CO2 is published hourly where
  prices can be quarter-hourly. It also carries `assumptions` *(1.2.1)* - the
  emission factors behind the number, with the source status of each; see
  above.
- **Combined score now** carries `score_series`: its current and upcoming
  published-price slots on one shared 0-100 scale. Use it to chart or inspect
  when the best combined price/CO2 periods are coming.
- **Next cheapest hour**, **Next weekend cheapest hour** and **Next cleanest
  hour** carry `hours`: the full locked plan as a list of
  `{start, end, average_value}`.
- The same two price sensors also carry `average`, `min`, `max` and
  `price_percent_to_average`, named after the Nordpool integration so its
  templates port across. They cover today's retained and upcoming published
  slots, never tomorrow or forecast values. `price_percent_to_average` is
  therefore "this quarter-hour against today's known price average" - 100%
  means average, 60% means clearly cheap. It is left empty when that average
  is zero or negative, for the same reason the plan saving is.
- **Current retail price** says where its number comes from:
  `retail_source` is `estimate` or `formula`, and with a formula
  `formula_factor` and `formula_surcharge` show the values in use *(1.6.0)*,
  and with a time-of-day tariff `network_charge_now` the grid charge it
  contains right now, without VAT *(1.7.0)*.

**To inspect them, use Developer tools > States**, pick the entity, and read
the attributes in the panel on the right. They will not show up in the history
or in the database (see below), so looking there is a dead end - you do not
need a throwaway automation that logs them.

These attributes are deliberately excluded from the recorder database since
0.7.2. They hold one entry per 15-minute slot across the whole horizon, which
exceeds the recorder's 16 KB per-state limit, so it used to drop them and log a
warning on every update. They are always available live for charts, templates
and automations - only their *history* is not stored.

The comparison snapshots above use a separate, bounded local storage file,
not recorder history. Removing the integration's configuration also removes
its comparison data. Changing the market, currency, price resolution or retail
formula starts a new comparison rather than mixing incompatible prices.

**No `recorder:` configuration is needed for this, and adding one is a bad
idea.** Excluding the whole entity (`recorder: exclude: entities:`) would also
discard its state, so you would lose the price history itself - a much bigger
loss than the attributes. If you are still seeing "State attributes ... exceed
maximum size" warnings, you are on a version older than 0.7.2; update instead
of configuring around it.
