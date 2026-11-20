# Your own price source *(1.21.0)*

How the integration reads day-ahead prices from Nord Pool or ENTSO-e, mixes
them into the forecast, and what it checks. Back to the
[README](../README.md#your-own-price-source-1210).

## What changes

With a source set, every slot of **Price forecast series** that your source
fully covers takes your source's price instead of our forecast. Everything
built on that series follows:

- **Current price** and **Price forecast series** show your source's price,
  and `raw_today` / `raw_tomorrow` carry it like any published price.
- The **cheapest-hours, weekend and dearest-hours plans** treat those hours
  as settled, exactly as they do with published prices. A plan that was
  built on the forecast is re-picked once when the prices arrive, as before.
- The **retail formula** and **time-of-day grid charges** are applied to it,
  so **Current retail price** contains your source's price plus your charges.
- **Published prices missing** stays off for an hour your source covers:
  the price for that hour is known, just not from us.
- The **Price source** sensor and the `price_source` attribute read
  `own_source` for such an hour.

Hours your source does not know yet stay our forecast, marked as such. The
morning hours of today that have already passed are filled from your source
as well, so a chart starts at midnight even in a zone where the API only has
a forecast.

Your source is read again whenever its sensor changes. Tomorrow's prices
therefore appear within seconds of reaching your source, without waiting
for the next poll of our API.

## What does not change

- **The cheapest, greenest and combined windows** are still chosen by the
  API, on its own data.
- **The retail estimate** (DE, NL, DK, AT, NO) comes ready-made from the API
  and is computed on our forecast; your source does not reach it. Use the
  own retail formula if you want both.
- **The forecast quality figures and `raw_forecast_reference`** compare our
  forecast with the prices our API received, not with your source.
- **Published prices from our API win.** Where both have a price for the
  same slot, ours stays. They come from the same auction and should be
  identical; where they differ by more than half a euro cent per kWh, the
  diagnostics list it.

## Supported sources

| Integration | Which sensor | How it is read |
| --- | --- | --- |
| Nord Pool, built into Home Assistant | any of its price sensors, e.g. *Current price* | the `nordpool.get_prices_for_date` action, in the currency of our series |
| [Nord Pool from HACS](https://github.com/custom-components/nordpool) | its price sensor | the `raw_today` and `raw_tomorrow` attributes |
| [ENTSO-e from HACS](https://github.com/JaccoR/hass-entso-e) | **Average electricity price** | the `prices_today` and `prices_tomorrow` attributes |

The dialog only offers sensors of these integrations. Hourly and
quarter-hourly sources both work: an hourly series takes the mean of four
quarter-hour prices, a quarter-hourly series takes the price of its hour.
Built-in Nord Pool is asked for today and tomorrow; a day that has arrived
is not asked for again, and a day that is still missing is asked for at most
every 15 minutes.

## Units and currency

The unit of the sensor is read and converted: `EUR/kWh`, `c/kWh`,
`EUR/MWh`, `€/kWh`, `SEK/kWh`, `Öre/kWh`, `NOK/kWh`, `Øre/kWh`, `DKK/kWh`,
`PLN/kWh` and the same per MWh or Wh. A unit that is not understood is never
guessed, and prices in another currency than our series are never mixed:
there is no exchange rate in between that both sides would agree on. If our
series is in euro and your source in krona, either set the source to euro or
tick **Show prices in local currency** in this integration's settings
(Czechia, Poland, Sweden). Built-in Nord Pool needs nothing: it is asked in
the currency of our series.

## The check when you save

The dialog reads the sensor once, the way the integration will, and compares
it with the series it would mix into.

- **Against published prices** the comparison is exact: if more than half of
  the slots differ by more than half a euro cent, the sensor probably adds
  VAT or charges, or is set to another area.
- **Against our forecast** only a large and steady offset counts: the median
  difference must exceed 2 euro cents per kWh *and* a fifth of the price
  level. VAT of 25 % plus a grid charge of a few cents is caught; an ordinary
  forecast miss is not.

Both of these, and a source that is momentarily unavailable or empty, can be
overruled by saving the same sensor a second time. A unit that is not
understood, another currency, or a sensor without a price list cannot.

## Diagnostics

The integration's diagnostics have an `own_price_source` section: the
sensor, its format and unit, a status (`ok`, `currency_mismatch`,
`unit_unknown`, `unknown_format`, `no_prices`, `unavailable`,
`service_failed`, `entity_missing`), how many slots were read and replaced,
and up to five slots where your source and our published prices disagree,
with both prices. Apart from those examples the source's prices are not
included. The file stays on your computer until you share it yourself.

## Switching it off

Open **Configure** again, clear the field and save. The series is then our
API's alone again.
