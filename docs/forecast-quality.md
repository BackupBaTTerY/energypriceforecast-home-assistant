# Forecast quality and what a plan is worth

The measured figures behind the quality entities, what to do when the
official prices are missing, and how to turn a plan into money saved.
Back to the [README](../README.md).

## When the official prices are missing *(1.15.0)*

**Published prices missing** is a binary sensor with Home Assistant's
`problem` device class, so it shows up red as "Problem" and can be used in an
automation as plainly as `state: "on"`.

It is on when the price for the slot running **right now** is a forecast
rather than a published day-ahead price. That is the narrow case, and
deliberately so: beyond the published window the series is always a forecast -
that is what this integration is for - and a flag that reported *that* would
be on every afternoon and worth nothing. The price for the current hour, on
the other hand, was settled at auction the day before. If it is a forecast,
the published prices never arrived.

That is not hypothetical. On 9 and 10 October 2026 the Dutch, Greek, Romanian
and Slovak day-ahead results were missing from both platforms this project
reads, for a day and a half. Installations in those markets were served a
complete series the whole time - all of it estimated.

Two attributes say what the flag rests on:

| Attribute | Meaning |
| --- | --- |
| `price_source` | `day_ahead`, `early_auction` or `forecast` for the current slot |
| `published_until` | end of the last published slot in your series, `null` while none is published |

An early-auction price does **not** raise the flag. It is not the official
price either, but it is a traded one: measured against the Dutch day-ahead
result for 10 October 2026 it sat 0.94 ct/kWh away, where the model forecast
sat 3.18 ct away. The attribute still names it, so a template can tell the
three cases apart.

What this is not: a repair issue, because there is nothing you can fix at your
end, and not a reason to make entities unavailable. The numbers are there,
they are only estimates, and an automation that stops dead helps nobody. What
an automation that spends money can usefully do is widen its margin or wait -
there is no point optimising to a third of a cent on a number that is a guess.
How long the flag has been on is simply the entity's own `last_changed`.

```yaml
automation:
  - alias: Charge the car, but not on guessed prices
    triggers:
      - trigger: state
        entity_id: binary_sensor.energy_price_forecast_eu_de_cheapest_hours_active
        to: "on"
    conditions:
      - condition: state
        entity_id: binary_sensor.energy_price_forecast_eu_de_published_prices_missing
        state: "off"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.wallbox
```

The dashboard card below carries it in two places. A badge at the top of the
view shows the flag itself, so the state is visible whichever card you are
looking at. And in the price tile there is one line that is always there: a
green check with "Official prices until 22:00" while the prices are
published, and a warning in its place, directly above the number, when they
are not.

The line in the tile finds the sensor by itself - it looks for the entity with
the `problem` device class on the same device as your price sensor, which works
whatever your entity IDs look like. The badge cannot do that: a badge needs a
literal entity ID, so that one line is the only part of the card you have to
point at your own entity. If you would rather not, delete the badge - the line
in the tile tells you the same thing.

## How good is the forecast, really?

*Added in 1.0.0 - if you do not see this entity, check your installed
version under HACS before looking anywhere else.*

The **Forecast quality** diagnostic entity answers that with measurements, not
claims. The API replays its own frozen forecast against the day-ahead prices
that were published afterwards, over a rolling window of complete days, and
reports how often it picked the right cheapest window - for your market and
for your configured best-window duration.

The entity's state is the part that costs money: the average extra price paid
by following the forecast's window instead of the one that turned out
cheapest. It is a number in your market's currency with a `state_class`, so
you can graph it - a rising line means the forecast is getting worse.

The counts sit in attributes: `exact_hit_days`, `within_one_hour_days`,
`evaluated_days`, `exact_hit_percent`, `within_one_hour_percent`,
`window_hours`, `price_basis`, `period_start`, `period_end`.

### Two more numbers *(1.8.0)*

**Forecast accuracy** answers a different question: not whether the right
window was picked, but by how much each hour was off. Its state is the mean
absolute error against the day-ahead prices published afterwards, over 30
days, in your market's currency. The attributes add the median, the 90th
percentile, the share of hours inside the API's two thresholds
(`within_primary_percent` and `within_secondary_percent`, with the
thresholds themselves next to them), the correlation `pearson_r` and the
`sample_count`.

The two entities can disagree, and in both directions: a window can be
picked correctly from prices that are all too high, and every price can be
close while the cheapest window is still missed. That is why they are
separate.

**Cheaper day decision** is for loads that can wait a day - the washing
machine, the car over a long weekend. The API replays each pair of days and
checks whether the day it recommended really held the cheaper window. The
state is that share in percent.

It stays unknown until thirty pairs are in, about a month, while the
attributes count the progress (`evaluated_pairs`, `minimum_ready_pairs`,
`ready`). They also say what a wrong day costs (`mean_regret_when_wrong`,
`median_regret_when_wrong`) and what waiting was worth across every
recommendation to wait, the ones that backfired included
(`mean_saving_when_waiting`, `median_saving_when_waiting`).

### As a banner on your dashboard

Add via **Dashboard > Edit > Add card > Manual**, replacing the entity:

```yaml
type: markdown
content: >-
  {% set q = 'sensor.CHANGE_ME_forecast_quality' %}
  {% if states(q) not in ['unknown', 'unavailable'] %}
    ### Forecast quality

    Cheapest {{ state_attr(q, 'window_hours') }}-hour window: within one hour
    on **{{ state_attr(q, 'within_one_hour_days') }} of
    {{ state_attr(q, 'evaluated_days') }} days**
    ({{ state_attr(q, 'within_one_hour_percent') }}%).

    Exactly right: {{ state_attr(q, 'exact_hit_days') }} days. Average extra
    price versus the perfect window:
    **{{ (states(q) | float * 100) | round(2) }} ct/kWh**.

    <sub>Frozen forecast against published day-ahead prices,
    {{ state_attr(q, 'period_start') }} to {{ state_attr(q, 'period_end') }}.
    Complete days only.</sub>
  {% else %}
    Forecast quality is unavailable - either this market has too little
    history yet, or the integration is older than 1.0.0.
  {% endif %}
```

The `| float * 100` converts EUR/kWh to ct/kWh for readability - drop it for
markets quoted in øre or DKK, where the raw unit is already small. The
`round(2)` is not cosmetic: without it the float arrives as
`0.43000000000000005`.

### What it does and does not cover

- **The contiguous cheapest window only.** Not the cheapest-hours plan, not
  the CO2 windows.
- **Spot prices only.** The API reports `price_basis: "base"` even when you
  request retail, because the replay runs on spot. Verified: the figure is
  byte-identical between a base and a retail request.
- **Your market.** Quality differs a lot between markets, so a number from
  somewhere else would say nothing about yours.
- When there is too little history the entity is **unavailable** rather than
  showing a zero, and the `error` attribute says why.

## What is the plan actually worth?

*Added in 1.0.0.*

Two sensors answer that, and they appear as soon as a cheapest-hours count is
set (the weekend plan gets its own pair):

- **Cheapest hours average price** - what the hours the plan picked cost on
  average.
- **Cheapest hours saving** - how far below the *whole block's* average that
  lands, in percent.

> Plan: 0.087 EUR/kWh - 38.7% below the block average of 0.142 EUR/kWh

Both are computed once, when the block's hours are picked, and are locked
together with the plan. They do not drift while the plan stays put, which is
what makes them worth putting on a dashboard. If the day-ahead auction
re-picks a forecast-planned block (see above), both are recomputed with it -
comparing settled picks against a guessed average would be meaningless.

**Read the saving for what it is.** It compares the picked hours against
running at an arbitrary time *in the same block* - it is not a comparison
against your old tariff, your neighbour, or a fixed-price contract, and it is
not money. Converting it to money needs your consumption, which this
integration does not know; see below for how to do that yourself.

**With retail pricing enabled, the plan is built and priced on the retail
series**, not the spot price. With the estimate that can move the hours it
picks; with the estimate or your own formula it changes the saving it
reports. That matters more than it sounds: the retail markup is a large
addition, so the same hours look far cheaper against a spot baseline than
against the price actually billed. On live German data, a genuine 43% retail
saving showed as 100% when measured on spot.

The saving is left empty when the block average is zero or negative. Negative
prices are routine in these markets, and "40% cheaper than -0.001 EUR/kWh"
states a number without stating anything true. On spot prices the saving can
also exceed 100%, which is not a bug: it means the picked hours pay you rather
than cost you.

### Turning it into euros

The average-price sensor is the piece you need; the rest is standard Home
Assistant. Multiply it by the energy your device actually used during the
planned hours:

```yaml
template:
  - sensor:
      - name: Heat pump cost during cheap hours
        unique_id: heat_pump_cheap_hour_cost
        unit_of_measurement: EUR
        state_class: total_increasing
        device_class: monetary
        state: >
          {% set price = states('sensor.CHANGE_ME_cheapest_hours_average_price')
             | float(0) %}
          {% set energy = states('sensor.CHANGE_ME_heat_pump_energy')
             | float(0) %}
          {{ (price * energy) | round(2) }}
```

If your prices are in your own currency *(1.4.0)*, use its code - `CZK`,
`PLN` or `SEK` - as the unit instead of `EUR`.

Point `sensor.CHANGE_ME_heat_pump_energy` at a kWh meter for the device - a
smart plug's energy sensor, or a `utility_meter` helper that you reset per
block. For a "what did I save" figure, build the same sensor a second time
using the `window_average_value` attribute of the average-price sensor as the
price, and subtract the two.

This is left as a recipe rather than a built-in feature on purpose: it needs
an entity only you can name, its correctness depends entirely on that meter
being the right one, and a wrong number here is worse than no number.

### Turning it into grams of CO2 *(1.2.0)*

The same shape, with the cleanest-hours plan. **Cleanest hours average CO2**
is in gCO2/kWh, so multiplying by kWh gives grams:

```yaml
template:
  - sensor:
      - name: Car charging CO2 during clean hours
        unique_id: car_charging_clean_hour_co2
        unit_of_measurement: g
        state_class: total_increasing
        state: >
          {% set intensity = states('sensor.CHANGE_ME_cleanest_hours_average_co2')
             | float(0) %}
          {% set energy = states('sensor.CHANGE_ME_wallbox_energy')
             | float(0) %}
          {{ (intensity * energy) | round(0) }}
```

For "what did charging at these hours avoid", build the same sensor with the
`window_average_value` attribute in place of the state - that is the block's
own average intensity - and subtract the two. The result is emissions avoided
**against charging at an arbitrary time in the same block**, which is the only
comparison this integration can support. It is not a comparison against the
grid average, against another country, or against not charging at all, and it
is not a carbon offset.
