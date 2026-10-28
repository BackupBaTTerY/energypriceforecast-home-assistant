# Tariffs and prices

How the integration turns the exchange price into the price on your bill:
another currency, a retail price, time-of-day grid charges, and contracts
that bill the hourly mean. Back to the [README](../README.md).

## Prices in your own currency *(1.4.0)*

For Czechia, Poland and Sweden the API answers in euro unless it is asked
for something else. Tick **Show prices in local currency** when you set the
integration up or reconfigure it, and every price it shows - current price,
windows, plans, the price series, the forecast-quality cost - arrives in
CZK, PLN or SEK instead.

| Market | With the option ticked |
|---|---|
| CZ | CZK |
| PL | PLN |
| SE1-SE4 | SEK |
| DK1, DK2, NO1-NO5 | DKK or NOK - already the default, ticked or not |
| all other markets | euro - the option changes nothing |

**Which exchange rate.** The conversion uses the European Central Bank's
daily reference rate, and the API picks up a new one within twelve hours of
its publication. Czech spot tariffs usually convert OTE's euro price with the
Czech National Bank's rate instead. On 11 September 2026 the two stood at
24.264 and 24.260 CZK per euro: 0.016% apart, a thousandth of a koruna per
kWh, far inside the error of any forecast.

**What switching does.** Home Assistant keeps each sensor's long-term
statistics in the sensor's unit, and it cannot convert between currencies.
When a price sensor's unit changes from EUR/kWh to CZK/kWh, Home Assistant
stops extending the old statistics and offers to fix them under
*Developer tools > Statistics* - usually by deleting them. Switching
therefore starts the price history afresh. A plan already picked for the
current block is picked once more in the new currency; the hours stay the
same, because converting every price with one rate does not change their
order.

Switzerland stays in euro: the API does not offer francs. Romania is
answered in euro too - there are no lei - and Bulgaria has been on the euro
since January 2026.

## Retail price: estimate or your own formula *(1.6.0)*

The day-ahead price is not what you pay. Taxes, grid charges and your
supplier's margin come on top, and they decide what a cheap hour is worth.
Choose a source under **Retail price** when you set the integration up or
reconfigure it:

| Choice | What you get | Markets |
|---|---|---|
| Off | Day-ahead prices only | all |
| Estimate | Our assumption-based all-in price: grid fee, levies, supplier markup and VAT. Germany needs a postal code for the local grid fee | DE, NL, DK1, DK2, AT, NO1-NO5 |
| Own formula | Day-ahead price × **factor** + **surcharge** | all |

Since *1.11.0* the choice decides what is asked next, on a page of its own:
**Own formula** asks for the factor and the surcharge, the **estimate** in
Germany asks for the postal code, and the estimate elsewhere and **Off** ask
for nothing more. Reconfiguring opens that page with the values in use. What
belongs to a source you switch away from is not kept: the entry stores only
what the chosen source uses, so no postal code is sent for a market that
prices nothing by it.

**The formula** is *retail price = day-ahead price × factor + surcharge*.

- **Factor** multiplies the day-ahead price. Your VAT goes here: 1.21 for
  21%, 1.19 for 19%, 1.25 for 25%. A supplier fee charged as a percentage of
  the exchange price goes here too - 5% on top, with 21% VAT, is
  1.05 × 1.21 = 1.2705.
- **Surcharge** is a fixed amount per kWh *including VAT*, added after the
  factor, in the unit your price sensors show: EUR/kWh in most markets,
  DKK or NOK in Denmark and Norway, CZK, PLN or SEK with
  [local currency](#prices-in-your-own-currency-140) ticked. Energy tax, a
  fixed supplier markup and any grid charge per kWh belong here. It may be
  negative, for a contract that discounts the exchange price.

A Dutch example: 21% VAT, 9.161 ct/kWh energy tax and a supplier markup of
2 ct/kWh, both before VAT. Factor **1.21**, surcharge
(0.09161 + 0.02) × 1.21 = **0.1350**. At a day-ahead price of 10 ct/kWh the
retail price is 0.10 × 1.21 + 0.1350 = 0.256 EUR/kWh.

**What it changes.** The [retail entities](../README.md#with-retail-pricing-enabled)
appear, and the cheapest-hours and weekend plans are priced in retail terms,
so their average price and saving match your bill. The hours they pick are
the ones the day-ahead price would pick: a positive factor and a fixed
surcharge cannot make one hour cheaper than another if it was not already. The saving shrinks, because the
surcharge adds the same amount to every hour - that smaller number is the
honest one. The formula is applied inside Home Assistant to prices the
integration fetches anyway, so it costs no extra request.

**Grid charges that change with the time of day** do not fit into one
fixed surcharge, and they are the one thing that does move a plan to other
hours - see [Time-of-day grid charges](#time-of-day-grid-charges-170).

**Estimate or formula?** The estimate needs nothing from you, but it rests
on assumptions: a typical grid fee, a typical supplier markup. The
formula is exactly your contract, as far as you know its numbers. Changing
the formula later starts the current block's plan afresh in the new
numbers, because a plan keeps the prices it was built on.

**Updating from 1.5.x.** A ticked *Retail pricing* box becomes **Estimate**,
and nothing about your prices, entities or plans changes. An unticked box
becomes **Off**.

## Time-of-day grid charges *(1.7.0)*

In much of Europe the network part of the bill no longer costs the same
around the clock. With your own formula it can now be added hour by hour:

*retail price = (day-ahead price + grid charge of this hour) × factor + surcharge*

The grid charge sits inside the factor because price sheets state it
without VAT, exactly like the day-ahead price. Choose a source under
**Time-of-day grid charges** - it needs **Own formula** as the retail price,
because the estimate already contains a grid fee of its own:

| Choice | Where the amounts come from | Markets |
|---|---|---|
| Off | nowhere | all |
| Enter windows and amounts | your grid operator's price sheet; in Norway prefilled from your operator | all |
| Denmark: look up my grid operator | Energinet's Energi Data Service, refreshed daily | DK1, DK2 |

**Who has such charges** (as of September 2026):

| Market | What changes with the time of day |
|---|---|
| DE | Section 14a EnWG *Modul 3*: three levels with windows each grid operator sets. It applies to the whole metering point, not only the wallbox or heat pump, needs a smart meter and comes together with Modul 1 |
| DK | Tarifmodel 3.0: night 00-06, peak 17-21, amounts change three times a year - looked up automatically |
| NO | Energy charge (*energiledd*) by day, night and weekend at most operators - prefilled |
| SE | *Höglasttid* on weekdays 06-22 from November to March at many operators |
| FI | Cheaper nights and weekends at many of the 78 operators |
| BE | Flanders: day rate Monday to Friday 07-22; Wallonia since 2026 also cheap 11-17 on every day |
| FR | HP/HC, with TURPE 7 four seasonal slots - the hours differ from meter to meter, your bill shows yours |
| PL | G12/G12w zones, set by each distribution operator |
| CZ | Low and high tariff, but switched by ripple control: the operator sets and can move the times, so a fixed schedule is only an approximation |
| AT, NL, IT, CH | Nothing to enter: a flat network charge, or no tariff that follows the exchange |

**The form** has two short steps. First the windows: when the low rate
starts and ends, when the peak rate starts and ends, whether weekends are
charged at the low rate, and optionally which months are winter. Hours are
the market's local time, a window runs from its first hour up to - not
including - its last, so 22 to 6 is the night, and equal hours mean there is
no such window. A winter from 11 to 3 crosses New Year as it should. Then
the amounts, only for the levels and seasons you actually set, per kWh and
without VAT; fixed per-kWh taxes stay in the surcharge.

**What it changes.** Factor and surcharge never change which hour is
cheapest. A time-of-day charge does, and that is the point: a cheap night
rate can make the night cheaper in total than the solar noon, even when the
exchange price says otherwise. Measured on Danish prices with a winter
tariff, the cheapest-hours plan moved on up to a third of the days. Only
the plans are chosen anew: the cheapest *window* still comes from the API,
which picks it on the exchange price, and is merely priced with the charge.
The
retail price sensor shows the charge it contains right now as
`network_charge_now`. Changing the tariff starts the current block's plan
afresh, like changing the formula.

**Denmark.** Pick your grid operator and tariff - most households have
*Nettarif C*. The list is built from the dataset itself, currently 29
operators. Some operators book a discount as a separate record; it is
matched by its code and added, because the sum is what you pay. The
tariffs are published months ahead and reloaded once a day, so a failed
reload changes nothing. If none could be loaded at all, the retail price is
shown as unavailable rather than too low. Amounts are in DKK/kWh without
VAT: set the factor to 1.25 and put elafgift, Energinet's tariffs and your
supplier's margin, including VAT, into the surcharge.

**Norway.** Pick your grid operator and the fields are filled in from the
volunteer collection [fri-nettleie](https://github.com/kraftsystemet/fri-nettleie)
(CC-BY-4.0), whose prices are stated without taxes - exactly what the
formula needs. They are a starting point, not a live source: when this was
written, the median entry had last been checked nine months earlier, so
compare them with your price sheet before saving. The list shows each
entry's date, and operators marked ⚠ have seasonal or several special
periods that are only partly prefilled. In Nordland, Troms and Finnmark
there is no VAT on electricity: use a factor of 1.0.

**Updating from 1.6.x** changes nothing: the new option starts off.

## Hourly or quarter-hourly tariff *(1.12.0)*

The exchanges price every quarter hour, but a large part of the dynamic
contracts still bills the **hourly mean** of those four quarters. Such a
household never pays a quarter-hour price: the cheapest quarter of an hour
costs them exactly as much as the most expensive one.

**Price resolution of your tariff** says which of the two you have:

| Choice | What you get |
|---|---|
| Tariff with a quarter-hourly electricity price *(default)* | The market's own slots, unchanged |
| Tariff with an hourly electricity price | Whole clock hours, averaged from the quarters |

With the hourly tariff the API folds the series onto clock hours before
anything is derived from it, so the current price, the cheapest window, the
plans and the combined score all use the price your bill uses. The running
hour is averaged over all four of its quarters, including the ones that have
already passed - that is the price being settled. The entities then also move
on the hour instead of four times within it.

Pick it only if your supplier really settles by the hour. If you are unsure,
the quarter-hourly setting is the safe one: it is what every entry has been
using so far.
