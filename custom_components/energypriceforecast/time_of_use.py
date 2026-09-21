"""Grid charges that change with the time of day.

Across Europe the network part of a bill is increasingly no longer one price
per kWh around the clock. Denmark's Tarifmodel 3.0 has a night, a day and an
evening rate; Germany's Modul 3 under section 14a EnWG has three levels whose
windows every grid operator sets for itself; Norway, Sweden, Finland,
Belgium, France and Poland all have a cheaper night, and often a cheaper
weekend. The amounts are not small: in a Danish January the network alone
costs 0.73 DKK/kWh in the evening peak and 0.08 at night.

That defeats a single fixed surcharge twice over. The retail price is wrong
by tens of oere in individual hours, and the plans pick the wrong hours -
measured on Danish prices with a winter tariff, up to a third of the days.

The rate belongs *inside* the factor of the retail formula, because grid
charges are published without VAT, exactly like the day-ahead price:

    retail = factor x (day-ahead + rate of this hour) + surcharge

Two shapes deliver such a rate, and both answer the same question - what does
the network cost at this moment:

- ``Schedule``: the windows and amounts the user copied from their price
  sheet. It is the only option in most markets, because outside Denmark and
  Norway no grid operator publishes its tariffs in machine-readable form.
- ``HourlyTable``: 24 published prices per day, the shape Denmark's operators
  publish through Energi Data Service.

Public holidays are deliberately not modelled. Denmark's tariff ignores them,
Germany's operators differ, and a wrong holiday calendar would move a plan on
exactly the days a household notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, Final, Mapping, Protocol

# What a weekend does to the rate. Flanders, Finland, Norway and Poland's
# G12w price the whole weekend at the night rate; Wallonia and most German
# operators do not treat it differently at all.
WEEKEND_LIKE_WEEKDAY: Final = "like_weekday"
WEEKEND_LOW: Final = "low"
WEEKEND_RULES: Final[tuple[str, ...]] = (WEEKEND_LIKE_WEEKDAY, WEEKEND_LOW)

LEVEL_LOW: Final = "low"
LEVEL_STANDARD: Final = "standard"
LEVEL_PEAK: Final = "peak"

_DIGITS: Final = 6


class TariffSource(Protocol):
    """Anything that can price the network for one moment."""

    def rate_at(self, moment: datetime) -> float | None:
        """The network charge per kWh at ``moment``, without VAT."""

    def key(self) -> str:
        """A short, stable name of this tariff, for a stored plan's key."""


def in_window(hour: int, start: int, end: int) -> bool:
    """Whether ``hour`` falls in the half-open window [start, end).

    Equal bounds mean "no window": that is how an unset peak window arrives
    here, and a window from 6 to 6 would otherwise cover either no hour or
    every hour, depending on which way it is read.
    """
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # Crosses midnight, as a night window usually does: 22 to 6.
    return hour >= start or hour < end


def in_month_range(month: int, first: int, last: int) -> bool:
    """Whether ``month`` lies in the inclusive range, which may wrap a year.

    Winter prices run November to March in Sweden and France, October to
    March in Denmark - ranges that cross New Year, where first > last.
    """
    if not first or not last:
        return False
    if first <= last:
        return first <= month <= last
    return month >= first or month <= last


@dataclass(frozen=True)
class Rates:
    """The three amounts of one season, per kWh and without VAT."""

    low: float
    standard: float
    peak: float

    def of(self, level: str) -> float:
        if level == LEVEL_LOW:
            return self.low
        if level == LEVEL_PEAK:
            return self.peak
        return self.standard


@dataclass(frozen=True)
class Schedule:
    """Windows and amounts as they stand on a grid operator's price sheet.

    A two-level tariff is the same thing with no peak window: leaving
    ``peak_start`` and ``peak_end`` equal means every hour outside the low
    window is charged the standard rate.
    """

    low_start: int
    low_end: int
    peak_start: int
    peak_end: int
    weekend: str
    summer: Rates
    zone: tzinfo
    winter: Rates | None = None
    winter_from: int = 0
    winter_to: int = 0

    def level_at(self, moment: datetime) -> str:
        """Which of the three levels applies, in the market's own clock."""
        local = moment.astimezone(self.zone)
        if self.weekend == WEEKEND_LOW and local.weekday() >= 5:
            return LEVEL_LOW
        hour = local.hour
        if in_window(hour, self.low_start, self.low_end):
            return LEVEL_LOW
        if in_window(hour, self.peak_start, self.peak_end):
            return LEVEL_PEAK
        return LEVEL_STANDARD

    def rates_at(self, moment: datetime) -> Rates:
        """The amounts of the season this moment falls in."""
        if self.winter is None:
            return self.summer
        month = moment.astimezone(self.zone).month
        if in_month_range(month, self.winter_from, self.winter_to):
            return self.winter
        return self.summer

    def rate_at(self, moment: datetime) -> float:
        return round(self.rates_at(moment).of(self.level_at(moment)), _DIGITS)

    def key(self) -> str:
        """Everything that changes a price, in one line.

        A stored plan keeps the prices it was built on, so a plan must not
        outlive the tariff it was priced with - see coordinator.plan_basis.
        """
        parts = [
            f"{self.low_start}-{self.low_end}",
            f"{self.peak_start}-{self.peak_end}",
            self.weekend,
            f"{self.summer.low:g}/{self.summer.standard:g}/{self.summer.peak:g}",
        ]
        if self.winter is not None:
            parts.append(
                f"{self.winter_from}-{self.winter_to}:"
                f"{self.winter.low:g}/{self.winter.standard:g}/{self.winter.peak:g}"
            )
        return "tou|" + "|".join(parts)


@dataclass(frozen=True)
class HourlyTable:
    """Published prices, one per hour of each day, keyed by local date.

    Denmark's grid operators publish exactly this through Energi Data
    Service, months ahead. A day that is not in the table has no rate rather
    than a guessed one: pricing an hour with last week's tariff would be a
    silent error, while a missing rate is visible.
    """

    prices: Mapping[date, tuple[float, ...]]
    zone: tzinfo
    name: str

    def rate_at(self, moment: datetime) -> float | None:
        local = moment.astimezone(self.zone)
        day = self.prices.get(local.date())
        if not day or len(day) < 24:
            return None
        value = day[local.hour]
        return None if value is None else round(float(value), _DIGITS)

    def key(self) -> str:
        # The amounts change with the season, but a plan never outlives its
        # block, so the tariff's name is enough to tell two tariffs apart.
        return f"tou|{self.name}"


def mean_rate(
    tariff: TariffSource, start: datetime, end: datetime
) -> float | None:
    """The average rate over a window, sampled hour by hour.

    The summary reports one average price for a whole window, and with a
    time-of-day tariff that window can span several levels. Sampling at the
    start of each hour matches how the tariff itself is defined.
    """
    if end <= start:
        return None
    total = 0.0
    samples = 0
    moment = start
    while moment < end:
        rate = tariff.rate_at(moment)
        if rate is None:
            return None
        total += rate
        samples += 1
        moment += timedelta(hours=1)
    return round(total / samples, _DIGITS) if samples else None


def schedule_from_config(data: Mapping[str, Any], zone: tzinfo) -> Schedule:
    """Build a schedule from the flat keys a config entry stores."""
    summer = Rates(
        low=float(data.get("tou_rate_low", 0.0)),
        standard=float(data.get("tou_rate_standard", 0.0)),
        peak=float(data.get("tou_rate_peak", 0.0)),
    )
    winter = None
    if data.get("tou_winter"):
        winter = Rates(
            low=float(data.get("tou_winter_rate_low", summer.low)),
            standard=float(data.get("tou_winter_rate_standard", summer.standard)),
            peak=float(data.get("tou_winter_rate_peak", summer.peak)),
        )
    return Schedule(
        low_start=int(data.get("tou_low_start", 0)),
        low_end=int(data.get("tou_low_end", 0)),
        peak_start=int(data.get("tou_peak_start", 0)),
        peak_end=int(data.get("tou_peak_end", 0)),
        weekend=str(data.get("tou_weekend", WEEKEND_LIKE_WEEKDAY)),
        summer=summer,
        zone=zone,
        winter=winter,
        winter_from=int(data.get("tou_winter_from", 0) or 0),
        winter_to=int(data.get("tou_winter_to", 0) or 0),
    )
