# Home batteries: EMHASS and Predbat

A battery optimiser decides when to charge from the grid and when to run on
the battery. It can only plan as far as it knows prices. Published day-ahead
prices end at midnight tomorrow; this integration adds the forecast beyond
that - 48 hours ahead on the free tier, 120 hours with Private Pro - so the
optimiser can see a cheap or an expensive day coming before it is published.

Both tools below read the same slots the price chart uses: the attributes
`raw_today` and `raw_tomorrow` (published prices) and `raw_forecast` (the
forecast for the hours after them), each a list of `{start, end, value}` in
the sensor's unit, for example EUR/kWh. They never overlap. See
[Attributes worth knowing](entities.md#attributes-worth-knowing).

Which sensor to use:

- **Current retail price** if you set up retail pricing - the all-in price you
  pay per kWh, which is what a charge-or-not decision should be made on.
- **Price forecast series** otherwise - the exchange price.

The examples use `sensor.energy_price_forecast_eu_de_current_retail_price`;
replace it with your own entity ID.

## EMHASS

EMHASS takes the import price as `load_cost_forecast` when you start an
optimisation. Its documentation recommends passing it as a mapping from
timestamp to price, which needs no fixed list length and handles quarter
hours and clock changes. Build that mapping from all three attributes:

```yaml
shell_command:
  emhass_dayahead_optim: >
    {% set src = 'sensor.energy_price_forecast_eu_de_current_retail_price' %}
    {% set slots = (state_attr(src, 'raw_today') or [])
      + (state_attr(src, 'raw_tomorrow') or [])
      + (state_attr(src, 'raw_forecast') or []) %}
    {% set prices = dict(zip(slots | map(attribute='start') | list,
      slots | map(attribute='value') | list)) %}
    curl -i -H "Content-Type: application/json" -X POST -d '{
      "load_cost_forecast": {{ prices | tojson }}
    }' http://localhost:5000/action/dayahead-optim
```

Notes:

- Timestamps carry their UTC marker (`Z`), so they are not mistaken for
  local time.
- `raw_today` also holds the hours already past since local midnight;
  EMHASS only uses the ones inside its optimisation horizon.
- Set `prod_price_forecast` the same way only if you are paid the market price
  for feeding in. With a fixed feed-in tariff, leave EMHASS's configured value.
- EMHASS fills a gap in the mapping by holding the neighbouring price, so a
  plan built on missing data still looks valid. Before calling it, you can
  check that the series reaches far enough, for example with a condition
  `{{ (state_attr(src, 'raw_forecast') or []) | count > 0 }}`.

The forecast beyond tomorrow is only useful if EMHASS plans that far: set its
prediction horizon accordingly.

## Predbat

Predbat reads rates from a sensor's `raw_today` and `raw_tomorrow` attributes
through `metric_octopus_import`, takes `start`, `end` and `value`, and turns a
value per kWh into cents itself. It does not read `raw_forecast`, so pointed at
the sensor directly it would stop at the last published price.

A template sensor hands it the forecast as part of `raw_tomorrow`. Add this to
`configuration.yaml`:

```yaml
template:
  - triggers:
      - trigger: state
        entity_id: sensor.energy_price_forecast_eu_de_current_retail_price
    variables:
      src: sensor.energy_price_forecast_eu_de_current_retail_price
    sensor:
      - name: Predbat import rate
        unique_id: predbat_import_rate_energypriceforecast
        unit_of_measurement: EUR/kWh
        state: "{{ states(src) }}"
        attributes:
          raw_today: "{{ state_attr(src, 'raw_today') or [] }}"
          raw_tomorrow: >-
            {{ (state_attr(src, 'raw_tomorrow') or [])
               + (state_attr(src, 'raw_forecast') or []) }}

recorder:
  exclude:
    entities:
      - sensor.predbat_import_rate
```

The state trigger fires on every update of the source sensor, attributes
included. The recorder exclusion keeps several days of slots out of your
history database; this integration does the same for its own sensors.

Then in Predbat's `apps.yaml`:

```yaml
  metric_octopus_import: 'sensor.predbat_import_rate'
```

Notes:

- Predbat plans in 30-minute steps by default and needs rates aligned to :00
  and :30. Quarter-hour prices work with `plan_interval_minutes: 15`.
- Predbat looks `forecast_hours` ahead (48 is its suggestion). Beyond that the
  extra forecast is simply not used.
- Predbat's own documentation centres on UK tariffs and pence; the conversion
  to cents works the same way for euro and for øre/öre.

This setup follows Predbat's source code (`fetch_octopus_rates`, November
2026). If you run it, a short note in the
[issue tracker](https://github.com/BackupBaTTerY/energypriceforecast-home-assistant/issues)
on how it went helps the next person.
