# Dashboard and charts

A ready-to-paste dashboard, a chart that marks the planned hours, and a
prompt that lets an AI assistant build a card for your own setup. Back to
the [README](../README.md).


Two sensors carry `raw_today` / `raw_tomorrow` attributes - a list of price
time slots, made for charting with the community card
[apexcharts-card](https://github.com/RomRider/apexcharts-card) (installed
separately via HACS): the always-on price series sensor (day-ahead/spot price)
and, if you enabled retail pricing during setup, the current retail price
sensor (the all-in price, estimated or from your own formula). Both also
carry a third attribute,
`raw_forecast`: the entries beyond the published day-ahead window - the actual
ML/weather-based forecast, richer with a longer configured horizon.

**If you enabled retail pricing, chart the retail sensor.** The spot price is
not what you pay, so a chart built on it will disagree with your bill. The
shape of both sensors' attributes is identical, so the card below works for
either - just point it at the right entity.

### Ready-to-paste card

Install **apexcharts-card 2.1.0 or newer** through HACS first.
Replace all three `sensor.CHANGE_ME` lines with your own entity (see
[Finding your entity IDs](../README.md#finding-your-entity-ids-first) - on a German
instance it is likely `sensor..._preisreihe` or `sensor..._aktueller_endkundenpreis`).
Add the card via **Dashboard > Edit > Add card > Manual**.

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Energy Price Forecast EU
graph_span: 3d
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
all_series_config:
  yaxis_id: price
  type: line
  curve: stepline
  extend_to: false
  stroke_width: 2
  float_precision: 4
  show:
    legend_value: false
series:
  # Replace ALL three placeholders with the SAME price or retail sensor.
  - entity: sensor.CHANGE_ME
    name: Known (day-ahead)
    color: "#43a047"
    data_generator: |
      const raw = [...(entity.attributes.raw_today ?? []),
                   ...(entity.attributes.raw_tomorrow ?? [])];
      const slots = raw.map(e => ({
        start: Date.parse(e.start), end: Date.parse(e.end), value: e.value
      })).filter(e => Number.isFinite(e.start) && Number.isFinite(e.end)
        && e.end > e.start && typeof e.value === 'number' && Number.isFinite(e.value))
        .sort((a, b) => a.start - b.start);
      const points = [];
      let previousEnd = null;
      for (const slot of slots) {
        if (previousEnd !== null && slot.start > previousEnd) {
          points.push([previousEnd, null], [slot.start - 1, null]);
        }
        points.push([slot.start, slot.value], [slot.end - 1, slot.value]);
        previousEnd = slot.end;
      }
      return points;
  - entity: sensor.CHANGE_ME
    name: Current forecast
    color: "#fb8c00"
    data_generator: |
      const raw = entity.attributes.raw_forecast ?? [];
      const slots = raw.map(e => ({
        start: Date.parse(e.start), end: Date.parse(e.end), value: e.value
      })).filter(e => Number.isFinite(e.start) && Number.isFinite(e.end)
        && e.end > e.start && typeof e.value === 'number' && Number.isFinite(e.value))
        .sort((a, b) => a.start - b.start);
      const points = [];
      let previousEnd = null;
      for (const slot of slots) {
        if (previousEnd !== null && slot.start > previousEnd) {
          points.push([previousEnd, null], [slot.start - 1, null]);
        }
        points.push([slot.start, slot.value], [slot.end - 1, slot.value]);
        previousEnd = slot.end;
      }
      return points;
  - entity: sensor.CHANGE_ME
    name: Saved forecast (first seen)
    color: "#1e88e5"
    stroke_dash: 5
    data_generator: |
      const raw = entity.attributes.raw_forecast_reference ?? [];
      const slots = raw.map(e => ({
        start: Date.parse(e.start), end: Date.parse(e.end),
        value: typeof e.final_value === 'number' ? e.final_value : e.value
      })).filter(e => Number.isFinite(e.start) && Number.isFinite(e.end)
        && e.end > e.start && typeof e.value === 'number' && Number.isFinite(e.value))
        .sort((a, b) => a.start - b.start);
      const points = [];
      let previousEnd = null;
      for (const slot of slots) {
        if (previousEnd !== null && slot.start > previousEnd) {
          points.push([previousEnd, null], [slot.start - 1, null]);
        }
        points.push([slot.start, slot.value], [slot.end - 1, slot.value]);
        previousEnd = slot.end;
      }
      return points;
```

The known-price line includes the elapsed part of today on the left of the
Now marker; tomorrow follows as soon as its day-ahead prices are published.
All series are `extend_to: false` on purpose: without it, apexcharts-card
stretches the last value to the edge of the graph, inventing prices that were
never forecast.

The **blue dashed line** is the first forecast Home Assistant saved for each
slot. The **orange line** is the current forecast and may change. When official
prices arrive, the orange line is replaced by the **green day-ahead line** for
those slots, while the saved blue forecast remains for comparison. It is not
a fixed-lead-time quality measurement: a slot might first have been captured
48 or 120 hours ahead, depending on your horizon and when HA was running.

Snapshots accumulate only after installing this improvement; an initially
empty blue line is normal. They stay visible throughout the target day, then
are pruned at market midnight. There is no multi-day historical archive here.
Retail snapshots contain the all-in value calculated when captured, not a
later recalculation. An API retail estimate is still an estimate, not your
supplier's billed rate. For markets outside EUR, change the axis title to the
sensor's actual unit. For the full 120-hour horizon use `graph_span: 6d`;
the chart never creates data beyond what your API access supplies.

Slots include their real end time, gaps remain gaps, and zero and negative
prices are valid. Official points are never copied into the forecast line.
The ready-to-paste YAML is also in
[examples/price-chart-comparison.yaml](../examples/price-chart-comparison.yaml).
The comparison idea was inspired by
[Frank Hamm's practical dashboard](https://derschreiben.de/strompreisprognosen-mit-energy-price-forecast-und-home-assistant/).

### Showing the planned cheap hours in the chart

If you configured a cheapest-hours count, add this series to mark the hours the
plan actually picked, so you can see at a glance whether the plan lines up with
the price valleys. Point it at your *Next cheapest hour* sensor (German:
`sensor..._naechste_guenstige_stunde`), and use the *Next weekend cheapest
hour* sensor for the weekend plan.

Add it as the **first** entry under `series:`, before the three price series -
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

### A card that answers what to do now *(1.13.0)*

The chart above shows the prices; this one shows the decision. It is a whole
dashboard view - current price against today's average, today's range, the best
window, the cheapest-hours plan, the chart, and underneath the three measured
quality figures, so the numbers and the evidence for them sit on one screen.
It needs `apexcharts-card` for the chart; everything else is built in.

Add it under **Settings > Dashboards > your dashboard > Edit > Raw configuration
editor** as a new view, or copy
[`examples/price-card.yaml`](../examples/price-card.yaml).

Three things to adapt:

- **The entity IDs.** They follow your Home Assistant language and your device
  name. The card uses the English ones of a German market entry
  (`sensor.energy_price_forecast_eu_de_...`). Look yours up under **Developer
  Tools > States**, filter for `energypriceforecast`, and replace the prefix
  everywhere.
- **Without retail pricing**, replace `_current_retail_price` with
  `_current_price` and drop the `_retail` suffix on the window entities. The
  card then shows the exchange price throughout.
- **Without a cheapest-hours plan**, remove the "Cheapest hours" series from the
  chart; the plan tile already says what to do and needs no change.

The tiles read the unit from the entity, so a market in DKK, NOK, SEK, CZK or
PLN shows its own currency instead of cents. The chart's left axis has no unit
label for the same reason - the caption under it names the price it draws.

One honest detail, repeated in the card's own caption: the chart draws the
retail price, while the measured figures below are computed on the exchange
price. The gap you see is therefore your tariff's version of the error the
numbers describe.

```yaml
title: Price
path: price
icon: mdi:lightning-bolt-outline
type: sections
max_columns: 3
badges:
  - type: entity
    entity: binary_sensor.energy_price_forecast_eu_de_published_prices_missing
    name: Prices
    state_content: state
sections:
  - type: grid
    cards:
      - type: heading
        heading: Now
        heading_style: title
        icon: mdi:clock-outline
      - type: markdown
        content: >-
          {% set epf = 'sensor.energy_price_forecast_eu_de' %} {% set p = epf ~
          '_current_retail_price' %} {% macro money(x) %}{% set u =
          state_attr(p, 'unit_of_measurement') or '' %}{% if u[:3] == 'EUR'
          %}{{ '%.1f' | format(x | float(0) * 100) }} ct{% else %}{{ '%.3f' |
          format(x | float(0)) }} {{ u }}{% endif %}{% endmacro %} {% set v =
          state_attr(p, 'price_percent_to_average') %}

          <ha-icon icon="mdi:lightning-bolt"></ha-icon> **Electricity price
          now**

          {% set ns = namespace(warn=none) %}{% for e in
          device_entities(device_id(p)) %}{% if e.startswith('binary_sensor.')
          and state_attr(e, 'device_class') == 'problem' %}{% set ns.warn = e
          %}{% endif %}{% endfor %}{% set warn = ns.warn %}{% if warn and
          is_state(warn, 'on') %}<ha-alert alert-type="warning">Estimated
          price: the official prices for this hour were never
          published.</ha-alert>{% elif warn %}{% set pu = state_attr(warn,
          'published_until') %}<ha-icon icon="mdi:check-decagram"
          style="color: var(--success-color)"></ha-icon> {% if pu is string
          %}Official prices until **{{ as_timestamp(pu) |
          timestamp_custom('%H:%M') }}**{% else %}Published prices in use{%
          endif %}{% endif %}

          {% if states(p) | is_number %}

          # {{ money(states(p)) }}

          {% if v is not none %}{% set d = (v - 100) | round | int %}{% if d <=
          -1 %}<ha-alert alert-type="success">{{ d | abs }}% below today's
          average</ha-alert>{% elif d >= 1 %}<ha-alert
          alert-type="warning">{{ d }}% above today's average</ha-alert>{% else
          %}<ha-alert alert-type="info">Right at today's average</ha-alert>{%
          endif %}{% endif %}

          {% set all = (state_attr(p, 'raw_today') or []) + (state_attr(p,
          'raw_tomorrow') or []) %}{% set ns = namespace(s=0, n=0) %}{% set t0
          = now() %}{% set t1 = now() + timedelta(hours=1) %}{% for e in all %}{%
          set st = as_datetime(e.start) %}{% if st is not none and st >= t0 and
          st < t1 and e.value is number %}{% set ns.s = ns.s + e.value %}{% set
          ns.n = ns.n + 1 %}{% endif %}{% endfor %}{% if ns.n %}Next hour
          averages **{{ money(ns.s / ns.n) }}**{% endif %}

          {% else %}

          # –

          <ha-alert alert-type="info">No price available right now</ha-alert>

          {% endif %}
      - type: markdown
        content: >-
          {% set s = 'sensor.energy_price_forecast_eu_de_combined_score_now' %}

          <ha-icon icon="mdi:scale-balance"></ha-icon> **How good is this
          moment**

          {% if states(s) | is_number %}

          ## {{ states(s) | round | int }} of 100

          on price and CO2 together, against the published hours ahead

          {% else %}

          Not available for this market.

          {% endif %}
  - type: grid
    cards:
      - type: heading
        heading: Today
        heading_style: title
        icon: mdi:calendar-today
      - type: grid
        columns: 2
        square: false
        cards:
          - type: markdown
            content: >-
              {% set b = 'sensor.energy_price_forecast_eu_de_current_price' %}
              {% macro money(x) %}{% set u = state_attr(b,
              'unit_of_measurement') or '' %}{% if u[:3] == 'EUR' %}{{ '%.1f' |
              format(x | float(0) * 100) }} ct{% else %}{{ '%.3f' | format(x |
              float(0)) }} {{ u }}{% endif %}{% endmacro %}

              Exchange price now

              ## {% if states(b) | is_number %}{{ money(states(b)) }}{% else %}–{%
              endif %}

              {% set p =
              'sensor.energy_price_forecast_eu_de_current_retail_price' %}{% if
              states(b) | is_number and states(p) | is_number and (states(p) |
              float - states(b) | float) > 0.0005 %}+ {{ money(states(p) | float
              - states(b) | float) }} grid fees and taxes = {{ money(states(p))
              }} now{% endif %}
          - type: markdown
            content: >-
              {% set p = 'sensor.energy_price_forecast_eu_de_current_retail_price'
              %} {% macro money(x) %}{% set u = state_attr(p,
              'unit_of_measurement') or '' %}{% if u[:3] == 'EUR' %}{{ '%.1f' |
              format(x | float(0) * 100) }} ct{% else %}{{ '%.3f' | format(x |
              float(0)) }} {{ u }}{% endif %}{% endmacro %}

              Today's average

              ## {% set a = state_attr(p, 'average') %}{% if a is not none %}{{
              money(a) }}{% else %}–{% endif %}
      - type: markdown
        content: >-
          {% set p = 'sensor.energy_price_forecast_eu_de_current_retail_price' %}
          {% macro money(x) %}{% set u = state_attr(p, 'unit_of_measurement') or
          '' %}{% if u[:3] == 'EUR' %}{{ '%.1f' | format(x | float(0) * 100) }}
          ct{% else %}{{ '%.3f' | format(x | float(0)) }} {{ u }}{% endif %}{%
          endmacro %}

          Today's range

          ## {% set lo = state_attr(p, 'min') %}{% set hi = state_attr(p, 'max')
          %}{% if lo is not none and hi is not none %}{{ money(lo) }} – {{
          money(hi) }}{% else %}–{% endif %}
  - type: grid
    cards:
      - type: heading
        heading: Plan
        heading_style: title
        icon: mdi:calendar-check
      - type: markdown
        content: >-
          {% set epf = 'sensor.energy_price_forecast_eu_de' %} {% set a = epf ~
          '_cheapest_window_average_retail_price' %} {% macro money(x) %}{% set
          u = state_attr(a, 'unit_of_measurement') or '' %}{% if u[:3] == 'EUR'
          %}{{ '%.1f' | format(x | float(0) * 100) }} ct{% else %}{{ '%.3f' |
          format(x | float(0)) }} {{ u }}{% endif %}{% endmacro %} {% set days =
          ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
          'Sunday'] %} {% macro day_word(t) %}{% set dd = (t.date() -
          now().date()).days %}{{ 'today' if dd == 0 else ('tomorrow' if dd == 1
          else ('the day after tomorrow' if dd == 2 else days[t.weekday()]))
          }}{% endmacro %} {% set s = states(epf ~
          '_cheapest_window_start_retail') %} {% set e = states(epf ~
          '_cheapest_window_end_retail') %}

          {% if as_datetime(s) is not none and as_datetime(e) is not none %}

          {% set sd = as_local(as_datetime(s)) %}{% set ed =
          as_local(as_datetime(e)) %}{% set h = (ed - sd).total_seconds() / 3600
          %}

          <ha-icon icon="mdi:clock-time-four-outline"></ha-icon> **Best {{ '%g' |
          format(h) }}-hour window**

          ## {{ sd.strftime('%H:%M') }} – {{ ed.strftime('%H:%M') }}

          {% if sd <= now() < ed %}running now{% else %}{{ day_word(sd) }}{%
          endif %}{% if states(a) | is_number %} · avg {{ money(states(a)) }}{%
          endif %}

          {% else %}

          <ha-icon icon="mdi:clock-time-four-outline"></ha-icon> **Best window**

          Calculated as soon as new prices arrive.

          {% endif %}
      - type: markdown
        content: >-
          {% set epf = 'sensor.energy_price_forecast_eu_de' %} {% set avg = epf ~
          '_cheapest_hours_average_price' %} {% macro money(x) %}{% set u =
          state_attr(avg, 'unit_of_measurement') or '' %}{% if u[:3] == 'EUR'
          %}{{ '%.1f' | format(x | float(0) * 100) }} ct{% else %}{{ '%.3f' |
          format(x | float(0)) }} {{ u }}{% endif %}{% endmacro %} {% set days =
          ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday',
          'Sunday'] %} {% macro day_word(t) %}{% set dd = (t.date() -
          now().date()).days %}{{ 'today' if dd == 0 else ('tomorrow' if dd == 1
          else ('the day after tomorrow' if dd == 2 else days[t.weekday()]))
          }}{% endmacro %} {% set nx_e = epf ~ '_next_cheapest_hour' %} {% set n
          = (state_attr(nx_e, 'hours') or []) | count %} {% set sp = states(epf ~
          '_cheapest_hours_saving') %} {% set active =
          is_state('binary_sensor.energy_price_forecast_eu_de_cheapest_hours_active',
          'on') %} {% set nx = as_datetime(states(nx_e)) %}

          {% if n %}

          <ha-icon icon="mdi:format-list-checks"></ha-icon> **The {{ n }}
          cheapest hours**

          ## {% if sp | is_number %}{{ '%.1f' | format(sp | float) }}% cheaper{%
          else %}–{% endif %}

          {% if active %}active now{% elif nx is not none %}{% set m =
          ((as_local(nx) - now()).total_seconds() / 60) | round | int %}{% if m <
          60 %}next one in {{ m }} minutes{% else %}next one {{
          day_word(as_local(nx)) }} at {{ as_local(nx).strftime('%H:%M') }}{%
          endif %}{% else %}none planned{% endif %}{% if states(avg) | is_number
          %} · avg {{ money(states(avg)) }}{% endif %}

          {% else %}

          <ha-icon icon="mdi:format-list-checks"></ha-icon> **Cheapest hours
          plan**

          Set a number of cheapest hours when you configure the integration.

          {% endif %}
  - type: grid
    column_span: 3
    cards:
      - type: heading
        heading: Price over the coming days
        heading_style: title
        icon: mdi:chart-line
      - type: custom:apexcharts-card
        header:
          show: false
        graph_span: 5d
        span:
          start: day
        now:
          show: true
          label: Now
        apex_config:
          chart:
            height: 340
            toolbar:
              show: false
            zoom:
              enabled: false
          legend:
            position: bottom
            horizontalAlign: left
            fontSize: 13px
          grid:
            strokeDashArray: 3
          xaxis:
            labels:
              datetimeFormatter:
                day: ddd dd MMM
                hour: HH:mm
          tooltip:
            x:
              format: ddd dd MMM HH:mm
        yaxis:
          - id: price
            decimals: 0
            apex_config:
              tickAmount: 5
          - id: plan
            show: false
            min: 0
            max: 1
        series:
          - entity: sensor.energy_price_forecast_eu_de_next_cheapest_hour
            name: Cheapest hours
            yaxis_id: plan
            type: area
            curve: stepline
            color: '#1e88e5'
            opacity: 0.15
            stroke_width: 0
            extend_to: false
            show:
              legend_value: false
            data_generator: |
              const raw = (entity.attributes.hours ?? []).map(h => ({
                start: new Date(h.start).getTime(),
                end: new Date(h.end).getTime(),
              })).sort((a, b) => a.start - b.start);
              const runs = [];
              for (const hour of raw) {
                const last = runs[runs.length - 1];
                if (last && hour.start <= last.end) last.end = Math.max(last.end, hour.end);
                else runs.push({ ...hour });
              }
              return runs.flatMap(run => [[run.start, 1], [run.end, 0]]);
          - entity: sensor.energy_price_forecast_eu_de_current_retail_price
            name: Published
            yaxis_id: price
            type: line
            curve: stepline
            color: '#43a047'
            stroke_width: 2
            extend_to: false
            show:
              legend_value: false
            data_generator: |
              // Euro markets read better in cents; everyone else keeps the
              // market's own unit, so the axis never claims the wrong currency.
              const f = (entity.attributes.unit_of_measurement ?? '').startsWith('EUR') ? 100 : 1;
              const known = [...(entity.attributes.raw_today ?? []),
                             ...(entity.attributes.raw_tomorrow ?? [])];
              return known.map(e => [new Date(e.start).getTime(), e.value * f]);
          - entity: sensor.energy_price_forecast_eu_de_current_retail_price
            name: Forecast
            yaxis_id: price
            type: line
            curve: stepline
            color: '#fb8c00'
            stroke_width: 2
            extend_to: false
            show:
              legend_value: false
            data_generator: |
              const f = (entity.attributes.unit_of_measurement ?? '').startsWith('EUR') ? 100 : 1;
              const known = [...(entity.attributes.raw_today ?? []),
                             ...(entity.attributes.raw_tomorrow ?? [])];
              const forecast = entity.attributes.raw_forecast ?? [];
              const join = known.length ? [known[known.length - 1]] : [];
              return [...join, ...forecast]
                .map(e => [new Date(e.start).getTime(), e.value * f])
                .sort((a, b) => a[0] - b[0]);
          - entity: sensor.energy_price_forecast_eu_de_current_retail_price
            name: Forecast before publication
            yaxis_id: price
            type: line
            curve: stepline
            color: '#1e88e5'
            stroke_width: 2
            extend_to: false
            show:
              legend_value: false
            data_generator: |
              const f = (entity.attributes.unit_of_measurement ?? '').startsWith('EUR') ? 100 : 1;
              const raw = entity.attributes.raw_forecast_reference ?? [];
              // Only where the price is already published: before that,
              // final_value follows every update and would lie on the forecast
              // line anyway.
              const known = [...(entity.attributes.raw_today ?? []),
                             ...(entity.attributes.raw_tomorrow ?? [])];
              const knownEnd = known.reduce((m, e) => Math.max(m, Date.parse(e.end) || 0), 0);
              const slots = raw.filter(e => Date.parse(e.start) < knownEnd).map(e => ({
                start: Date.parse(e.start), end: Date.parse(e.end),
                value: e.final_value ?? e.value
              })).filter(e => Number.isFinite(e.start) && Number.isFinite(e.end)
                && e.end > e.start && typeof e.value === 'number' && Number.isFinite(e.value))
                .sort((a, b) => a.start - b.start);
              const points = [];
              let previousEnd = null;
              for (const slot of slots) {
                if (previousEnd !== null && slot.start > previousEnd) {
                  points.push([previousEnd, null], [slot.start - 1, null]);
                }
                points.push([slot.start, slot.value * f], [slot.end - 1, slot.value * f]);
                previousEnd = slot.end;
              }
              return points;
            stroke_dash: 5
        grid_options:
          columns: full
      - type: markdown
        text_only: true
        content: >-
          <sub>Retail price per slot. Green: the published day-ahead prices;
          orange: the forecast for the days after. Dashed blue over the green
          line: the last forecast before the price was published. The figures
          below measure that same gap on the exchange price, so your tariff
          scales it.</sub>
        grid_options:
          columns: full
  - type: grid
    column_span: 3
    cards:
      - type: heading
        heading: How good is the forecast?
        heading_style: title
        icon: mdi:target
      - type: grid
        columns: 3
        square: false
        cards:
          - type: markdown
            content: >-
              {% set q = 'sensor.energy_price_forecast_eu_de_forecast_quality' %}

              Best window hit

              ## {% if state_attr(q, 'evaluated_days') %}{{ state_attr(q,
              'within_one_hour_days') }} of {{ state_attr(q, 'evaluated_days') }}
              days{% else %}–{% endif %}

              within one hour of the real cheapest start
          - type: markdown
            content: >-
              {% set a = 'sensor.energy_price_forecast_eu_de_forecast_accuracy' %}
              {% macro money(x) %}{% set u = state_attr(a, 'unit_of_measurement')
              or '' %}{% if u[:3] == 'EUR' %}{{ '%.1f' | format(x | float(0) *
              100) }} ct{% else %}{{ '%.3f' | format(x | float(0)) }} {{ u }}{%
              endif %}{% endmacro %}

              Error per hour

              ## {% if states(a) | is_number %}avg {{ money(states(a)) }}{% else
              %}–{% endif %}

              {% if state_attr(a, 'median_abs_error') is not none %}median {{
              money(state_attr(a, 'median_abs_error')) }}{% endif %}
          - type: markdown
            content: >-
              {% set d = 'sensor.energy_price_forecast_eu_de_cheaper_day_decision'
              %}

              Charge today or tomorrow?

              {% if state_attr(d, 'ready') %}## {{ state_attr(d,
              'correct_day_count') }} of {{ state_attr(d, 'evaluated_pairs') }}
              correct

              picking the cheaper day{% else %}## still learning

              {{ state_attr(d, 'evaluated_pairs') or 0 }} of {{ state_attr(d,
              'minimum_ready_pairs') or 30 }} day pairs{% endif %}
        grid_options:
          columns: full
      - type: markdown
        text_only: true
        content: >-
          <sub>Measured over the last 30 days: the frozen forecast against the
          day-ahead prices published later, on the exchange price. Forecast by
          [energypriceforecast.eu](https://energypriceforecast.eu/en/).</sub>

          {% set a = 'sensor.energy_price_forecast_eu_de_forecast_accuracy' %}
          {% set ns = namespace(h=none) %}{% for e in
          device_entities(device_id(a)) %}{% if state_attr(e, 'icon') ==
          'mdi:clock-check-outline' %}{% set ns.h = states(e) %}{% endif %}{%
          endfor %}{% if ns.h is not none and ns.h | is_number and ns.h | float
          < 120 %}<sub>This view looks {{ ns.h | int }} hours ahead. [Up to 120
          hours with Private Pro](https://energypriceforecast.eu/en/private-pro/)
          - 14 days free. [What the extra days were worth in your
          market](https://energypriceforecast.eu/en/forecast-quality/)</sub>{%
          endif %}
        grid_options:
          columns: full
```

### Or let an AI build it for you

Paste the prompt below into your AI assistant of choice to get a card tailored
to your setup - useful if you want a different layout, only today's prices, or
CO2 alongside the price.

<details>
<summary>Show the copyable prompt</summary>

```
Help me build a Home Assistant Lovelace card that charts electricity prices from the Energy Price Forecast EU integration using the apexcharts-card custom card.

The integration creates a sensor whose entity_id ends in "_price_series" (day-ahead/spot price, the exact name depends on my chosen market, for example sensor.energy_price_forecast_eu_de_price_forecast_series) and, if I enabled retail pricing, a second sensor ending in "_retail_current_price" (all-in price, either the integration's estimate or my own formula) with the same attribute shape. Each sensor's state is its current price; its attributes raw_today, raw_tomorrow and raw_forecast are each a list of objects shaped like {"start": ISO8601 timestamp, "end": ISO8601 timestamp, "value": number}. raw_today retains the published slots it has seen since local midnight so a chart keeps its history left of the Now marker; raw_tomorrow contains tomorrow's published prices once available. Both contain known prices, never estimates. raw_forecast holds only the entries beyond the published day-ahead window - the actual ML/weather-based forecast. The value's unit matches the market's currency (for example EUR/kWh).

Entity IDs follow the Home Assistant language, so do not guess them from the English names above - on a German instance the price series is sensor..._preisreihe and the retail price is sensor..._aktueller_endkundenpreis. Ask me for the exact ID rather than assuming one.

My actual entity_id is: <PASTE YOUR ENTITY ID HERE - find it under Settings > Devices & Services > Energy Price Forecast EU, or Developer Tools > States, filtering for "energypriceforecast">

If you build tiles rather than a chart, five rules keep the card honest. Read the unit from the entity (state_attr(entity, 'unit_of_measurement')) instead of assuming cents - markets settle in EUR, DKK, NOK, SEK, CZK or PLN. Derive the best-window length from the start and end sensors rather than writing "4-hour" into the text, because the duration is configurable in quarter hours. Derive the number of cheapest hours from the "hours" attribute of the next-cheapest-hour sensor instead of writing a number. For raw_forecast_reference use final_value and fall back to value - final_value is the last forecast before the official price arrived, which is the moment the published quality figures measure, while value is whatever this installation happened to see first. And give every tile a visible state for "no number yet": cheaper_day_decision stays unknown until 30 day pairs are scored, price_percent_to_average is empty when the day's average is zero or negative, and Slovakia and Romania have no CO2 at all.

Before writing YAML, ask me:
1. Do I already have HACS and the apexcharts-card custom card installed? If not, tell me to install apexcharts-card via HACS first (category: Frontend/Plugin).
2. Did I enable retail pricing? If I did, chart the retail all-in price rather than the spot price unless I explicitly say otherwise - the spot price is not what I pay, so a chart built on it will disagree with my bill. Ask me which entity that is instead of guessing the ID.
3. Should the chart show today only, today and tomorrow together, or the known prices plus the forecast (raw_today + raw_tomorrow + raw_forecast) as two visually distinct series (e.g. solid vs dashed, different colors)?
4. Did I configure a cheapest-hours count or a weekend plan? If so, offer to mark the planned hours in the chart, using the "hours" attribute of the corresponding sensor (a list of {"start", "end", "average_value"}). Seeing the plan against the price curve is usually the point of the chart.
5. Do I want a bar chart per hour or a line/area chart?
6. Do I want to use this to actually switch a device (heat pump, water heater, car charger), rather than only look at it? If so, say that a chart alone will not do that, and offer to write a matching automation triggered on the binary sensor that is on during the planned hours.
7. Do I have a load that can wait for days, such as an electric car or a home battery? If so, ask me for the state of the "Allowed horizon" diagnostic sensor (unit h). If it is below 120, tell me that Private Pro extends the forecast to 120 hours (14 days free), that the section "What does a longer horizon add compared with 48 hours?" on https://energypriceforecast.eu/en/forecast-quality/?market=<MY ZONE> shows what the extra days were worth in my zone, and that a 120-hour chart needs graph_span: 6d. Quote those figures if you can open the page; do not promise a saving beyond them.

Rules for your result:
- The same two price sensors also expose raw_forecast_reference: {start, end, value, captured_at, final_value, final_captured_at} slots. value is the first forecast received for each future slot; final_value is the last forecast before the official price arrived and is the value to draw, because the published quality figures measure that same moment. These survive restarts and official publication and are removed after the target market day ends. captured_at and final_captured_at are HA observation times, not model creation times. Offer an optional dashed reference line to compare that saved prediction against official prices; no predictions can be recovered from before the integration began saving them.
- Use only raw_today / raw_tomorrow / raw_forecast / raw_forecast_reference. Do not invent other attributes or a different data shape.
- Use apexcharts-card's data_generator to turn the attribute list into a chart series - do not assume the card accepts the attribute directly as a series.
- If I asked for known prices and forecast as separate series, use two series against the same entity (one summing raw_today+raw_tomorrow, one for raw_forecast), each with its own data_generator, and set extend_to: false on both - otherwise apexcharts-card visually extends the last value to the edge of the graph, which is misleading here.
- Draw each slot from start through end (ending one millisecond before end avoids duplicate timestamps). Preserve real gaps using null points. Do not prepend official prices to a forecast series or hide zero/negative prices. Use the sensor's unit consistently.
- Quote any string value (title, name, tooltip format) that itself contains a colon, like "Known: forecast" or "dd.MM. HH:mm" - an unquoted colon inside a YAML value breaks parsing.
- Produce a complete, correctly indented YAML block for a manual Lovelace card (type: custom:apexcharts-card).
- Tell me exactly where to paste it (Dashboard > Edit > Add card > Manual).
- If information is missing, ask - do not guess my entity_id or market.
```

</details>
