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

import re
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


# A window in minutes after local midnight, half-open like ``in_window``.
# The end is kept modulo a day, so "18:00-24:00" is (1080, 0) - the same
# window a whole-hour entry "18 to 0" always described.
Window = tuple[int, int]
Windows = tuple[Window, ...]

_DAY_MINUTES: Final = 24 * 60
_QUARTER: Final = 15
_WINDOW_RE: Final = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*[-–—]\s*(\d{1,2})(?::(\d{2}))?\s*$"
)


class InvalidWindows(ValueError):
    """The text is not a list of quarter-hour windows such as 06:00-08:00."""


def _minutes(hours: str, minutes: str | None) -> int:
    value = int(hours) * 60 + int(minutes or 0)
    if int(minutes or 0) > 59 or value > _DAY_MINUTES:
        raise InvalidWindows(f"{hours}:{minutes} is not a time of day")
    if value % _QUARTER:
        # Meters count in quarter hours, and so do the price sheets: a time
        # off the quarter is a typo, not a tariff.
        raise InvalidWindows(f"{hours}:{minutes} is not on the quarter hour")
    return value


def parse_windows(text: str | None) -> Windows:
    """Read ``06:00-08:00, 17:15-20:45`` into windows of minutes.

    Germany's Modul 3 lets every grid operator set its own windows, and some
    set two peak windows a day or start one at a quarter past - exactly what
    a single pair of whole hours cannot hold. Bare hours (``22-6``) are
    accepted, as are en dashes copied from a price sheet; commas, semicolons
    and line breaks separate windows. An empty text means no window.
    """
    windows: list[Window] = []
    for part in re.split(r"[,;\n]+", text or ""):
        if not part.strip():
            continue
        match = _WINDOW_RE.match(part)
        if match is None:
            raise InvalidWindows(f"{part.strip()!r} is not a window like 06:00-08:00")
        start = _minutes(match.group(1), match.group(2))
        end = _minutes(match.group(3), match.group(4))
        start, end = start % _DAY_MINUTES, end % _DAY_MINUTES
        if start == end:
            raise InvalidWindows(f"{part.strip()!r} is empty or the whole day")
        windows.append((start, end))
    return tuple(windows)


def windows_from_hours(start: int, end: int) -> Windows:
    """The one whole-hour window an entry from before 1.19.0 describes."""
    start, end = int(start) % 24, int(end) % 24
    return () if start == end else ((start * 60, end * 60),)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_windows(windows: Windows) -> str:
    """The text the form shows, and stores: ``06:00-08:00, 17:15-20:45``."""
    return ", ".join(f"{_clock(start)}-{_clock(end)}" for start, end in windows)


def in_windows(minute: int, windows: Windows) -> bool:
    """Whether a minute of the day falls in any of the windows."""
    for start, end in windows:
        if start < end:
            if start <= minute < end:
                return True
        elif minute >= start or minute < end:
            return True
    return False


def windows_overlap(first: Windows, second: Windows) -> bool:
    """Whether a quarter hour lies in both - every window is on the quarter."""
    return any(
        in_windows(minute, first) and in_windows(minute, second)
        for minute in range(0, _DAY_MINUTES, _QUARTER)
    )


def _key_part(windows: Windows) -> str:
    """One level's windows in a stored plan's key.

    A single whole-hour window keeps the exact form keys had before 1.19.0
    ("0-6", "0-0" for none), so updating does not discard the plans of every
    entry whose tariff did not change.
    """
    if not windows:
        return "0-0"

    def hour(minutes: int) -> str:
        return str(minutes // 60) if minutes % 60 == 0 else _clock(minutes)

    return ",".join(f"{hour(start)}-{hour(end)}" for start, end in windows)


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

    A two-level tariff is the same thing with no peak window: with ``peak``
    empty, every moment outside the low windows is charged the standard
    rate. Each level may have several windows, on the quarter hour.
    """

    low: Windows
    peak: Windows
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
        minute = local.hour * 60 + local.minute
        if in_windows(minute, self.low):
            return LEVEL_LOW
        if in_windows(minute, self.peak):
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
            _key_part(self.low),
            _key_part(self.peak),
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
    """The average rate over a window, sampled quarter hour by quarter hour.

    The summary reports one average price for a whole window, and with a
    time-of-day tariff that window can span several levels. Since 1.19.0 a
    level can start at a quarter past, so an hour can be a quarter peak and
    three quarters standard; sampling every quarter hour prices exactly
    that, and gives the old result for tariffs in whole hours.
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
        moment += timedelta(minutes=_QUARTER)
    return round(total / samples, _DIGITS) if samples else None


def stored_windows(data: Mapping[str, Any], level: str) -> Windows:
    """One level's windows from a config entry, old or new.

    Since 1.19.0 an entry stores the windows as text (``tou_low_windows``);
    entries from before keep their two whole hours (``tou_low_start`` and
    ``tou_low_end``) and are read exactly as before - nobody has to set the
    tariff up again. The text was checked when it was entered; should it
    still not parse, the level has no window rather than breaking setup.
    """
    text = data.get(f"tou_{level}_windows")
    if text is not None:
        try:
            return parse_windows(str(text))
        except InvalidWindows:
            return ()
    return windows_from_hours(
        data.get(f"tou_{level}_start", 0) or 0, data.get(f"tou_{level}_end", 0) or 0
    )


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
        low=stored_windows(data, "low"),
        peak=stored_windows(data, "peak"),
        weekend=str(data.get("tou_weekend", WEEKEND_LIKE_WEEKDAY)),
        summer=summer,
        zone=zone,
        winter=winter,
        winter_from=int(data.get("tou_winter_from", 0) or 0),
        winter_to=int(data.get("tou_winter_to", 0) or 0),
    )
