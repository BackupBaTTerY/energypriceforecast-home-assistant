"""Day-ahead prices from another integration, mixed into the forecast series.

New in 1.21.0. In 18 zones the API may only pass on its forecast, and even
where it passes on the official prices its free source can be hours late.
Many households already run an integration that fetches the day-ahead prices
itself - Nord Pool or ENTSO-e. With "own price source" set, those prices take
the place of forecast hours they cover; every later hour stays a forecast.
Nothing read here ever leaves Home Assistant.

Everything in this module is plain Python, so it can be tested without Home
Assistant: reading the three formats, converting units, mixing, and the
check the settings form runs before it saves.

The one rule the module depends on: the source must give the *bare*
exchange price. Grid charges, VAT and a retailer's margin are added by this
integration's own formula, to every hour alike. If the source already added
them, they would only reach today and tomorrow - the hours the source knows -
and the forecast days after would look cheaper than they are.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from math import isfinite
from statistics import median
from typing import Any

# What a replaced forecast slot says it is. Anything but "forecast" counts as
# a known price everywhere - plans settle on it, raw_today shows it, the
# "prices not published" flag turns off - which is exactly right: the price
# is known, just not from us.
OWN_SOURCE: str = "own_source"

FORMAT_NORDPOOL: str = "nordpool"  # raw_today / raw_tomorrow: start, end, value
FORMAT_ENTSOE: str = "entsoe"  # prices_today / prices_tomorrow: time, price
FORMAT_NORDPOOL_CORE: str = "nordpool_core"  # the get_prices_for_date service

# Half a euro cent per kWh in each currency the series can be in: the
# difference at which the same auction price, read from two places, counts as
# a real disagreement and not as rounding or a different exchange rate.
_TOLERANCE_PER_KWH: dict[str, float] = {
    "EUR": 0.005,
    "SEK": 0.06,
    "NOK": 0.06,
    "DKK": 0.037,
    "PLN": 0.021,
    "CZK": 0.125,
    "GBP": 0.0043,
    "CHF": 0.0047,
    "RON": 0.025,
    "BGN": 0.0098,
    "HUF": 2.0,
}
_DEFAULT_TOLERANCE = 0.005

# Against a forecast only a large and steady offset means anything: the
# forecast itself is a few cents off on an ordinary day. Four times the
# tolerance, or a fifth of the price level, whichever is larger - VAT of 25 %
# or a grid charge of a few cents passes either, a forecast miss rarely does
# across a whole day.
_FORECAST_TOLERANCE_FACTOR = 4.0
_FORECAST_TOLERANCE_SHARE = 0.2
# Fewer slots than this are too few to judge a level by.
_MIN_PAIRS = 4
_MAX_LISTED = 5

# A sensor's currency word, and how many of them make one unit of the
# currency. "Øre" is Danish and Norwegian alike; which one is meant follows
# from the series it is mixed into.
_CURRENCY_WORDS: dict[str, tuple[str | None, float]] = {
    "EUR": ("EUR", 1.0),
    "€": ("EUR", 1.0),
    "C": ("EUR", 0.01),
    "CT": ("EUR", 0.01),
    "CENT": ("EUR", 0.01),
    "CENTS": ("EUR", 0.01),
    "€C": ("EUR", 0.01),
    "SEK": ("SEK", 1.0),
    "ÖRE": ("SEK", 0.01),
    "NOK": ("NOK", 1.0),
    "DKK": ("DKK", 1.0),
    "ØRE": (None, 0.01),
    "PLN": ("PLN", 1.0),
    "GR": ("PLN", 0.01),
    "CZK": ("CZK", 1.0),
    "KČ": ("CZK", 1.0),
    "GBP": ("GBP", 1.0),
    "£": ("GBP", 1.0),
    "P": ("GBP", 0.01),
    "PENCE": ("GBP", 0.01),
    "CHF": ("CHF", 1.0),
    "RP": ("CHF", 0.01),
    "RON": ("RON", 1.0),
    "BANI": ("RON", 0.01),
    "BGN": ("BGN", 1.0),
    "HUF": ("HUF", 1.0),
}
_ENERGY_WORDS: dict[str, float] = {"KWH": 1.0, "MWH": 0.001, "WH": 1000.0}


@dataclass(frozen=True)
class OwnSlot:
    """One price of the source, already in the unit of the series."""

    start: datetime
    end: datetime
    value: float


@dataclass
class MergeReport:
    """What mixing did, for the diagnostics."""

    replaced: int = 0
    added: int = 0
    compared: int = 0
    disagreements: list[dict[str, Any]] = field(default_factory=list)
    disagreement_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "replaced_forecast_slots": self.replaced,
            "added_slots": self.added,
            "compared_with_published": self.compared,
            "disagreements": self.disagreement_count,
            "disagreement_examples": self.disagreements,
        }


def tolerance(currency: str | None) -> float:
    """Half a euro cent per kWh, in the given currency."""
    return _TOLERANCE_PER_KWH.get((currency or "").upper(), _DEFAULT_TOLERANCE)


def parse_unit(
    unit: Any, series_currency: str | None = None
) -> tuple[str, float] | None:
    """The currency of a price unit, and the factor to that currency per kWh.

    "c/kWh" is (EUR, 0.01), "NOK/MWh" is (NOK, 0.001). None for anything not
    understood - a unit is never guessed, a wrong factor of a hundred would
    turn every plan upside down.
    """
    if not isinstance(unit, str) or "/" not in unit:
        return None
    money, _, energy = unit.strip().partition("/")
    word = money.strip().upper()
    scale = _ENERGY_WORDS.get(energy.strip().upper())
    known = _CURRENCY_WORDS.get(word)
    if scale is None or known is None:
        return None
    currency, factor = known
    if currency is None:
        # "Øre" alone does not say which krone; the series it joins does.
        if (series_currency or "").upper() in ("DKK", "NOK"):
            currency = series_currency.upper()  # type: ignore[union-attr]
        else:
            return None
    return currency, factor * scale


def _moment(value: Any) -> datetime | None:
    """A timestamp from a datetime or its text, only with a time zone."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return moment if moment.tzinfo is not None else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if isfinite(number) else None


def detect_format(attributes: Mapping[str, Any] | None) -> str | None:
    """Which of the attribute formats a sensor carries, if any."""
    if not attributes:
        return None
    if isinstance(attributes.get("raw_today"), list):
        return FORMAT_NORDPOOL
    if isinstance(attributes.get("prices_today"), list) or isinstance(
        attributes.get("prices"), list
    ):
        return FORMAT_ENTSOE
    return None


def _nordpool_slots(attributes: Mapping[str, Any]) -> list[tuple[datetime, datetime, float]]:
    slots = []
    for key in ("raw_today", "raw_tomorrow"):
        entries = attributes.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            start = _moment(entry.get("start"))
            end = _moment(entry.get("end"))
            value = _number(entry.get("value"))
            if start is None or end is None or value is None or end <= start:
                continue
            slots.append((start, end, value))
    return slots


def _entsoe_slots(attributes: Mapping[str, Any]) -> list[tuple[datetime, datetime, float]]:
    """ENTSO-e names only the start; the end is where the next one starts.

    The integration runs hourly or every quarter hour. The step is the
    smallest distance between two starts, so a missing slot shortens nothing.
    """
    starts: dict[datetime, float] = {}
    for key in ("prices", "prices_today", "prices_tomorrow"):
        entries = attributes.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            start = _moment(entry.get("time"))
            value = _number(entry.get("price"))
            if start is not None and value is not None:
                starts[start.astimezone(timezone.utc)] = value
    ordered = sorted(starts)
    steps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
    step = min(steps) if steps else timedelta(hours=1)
    step = min(step, timedelta(hours=1))
    return [(start, start + step, starts[start]) for start in ordered]


def slots_from_attributes(
    attributes: Mapping[str, Any] | None,
) -> tuple[str | None, list[tuple[datetime, datetime, float]]]:
    """The format, and the raw (start, end, value) slots in the sensor's unit."""
    found = detect_format(attributes)
    if found == FORMAT_NORDPOOL:
        return found, _nordpool_slots(attributes or {})
    if found == FORMAT_ENTSOE:
        return found, _entsoe_slots(attributes or {})
    return None, []


def slots_from_service_response(
    response: Mapping[str, Any] | None, area: str
) -> list[tuple[datetime, datetime, float]]:
    """Slots from Nord Pool's get_prices_for_date, in its unit: per MWh."""
    entries = (response or {}).get(area)
    if not isinstance(entries, list):
        return []
    slots = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        start = _moment(entry.get("start"))
        end = _moment(entry.get("end"))
        value = _number(entry.get("price"))
        if start is None or end is None or value is None or end <= start:
            continue
        slots.append((start, end, value))
    return slots


def convert(
    raw: Sequence[tuple[datetime, datetime, float]], factor: float
) -> list[OwnSlot]:
    """Bring raw slots into the series unit, sorted and without duplicates."""
    unique: dict[datetime, OwnSlot] = {}
    for start, end, value in raw:
        key = start.astimezone(timezone.utc)
        unique[key] = OwnSlot(
            start=key, end=end.astimezone(timezone.utc), value=value * factor
        )
    return [unique[key] for key in sorted(unique)]


def _covered_average(
    slots: Sequence[OwnSlot], start: datetime, end: datetime
) -> float | None:
    """The time-weighted mean over [start, end), only if fully covered.

    An hour of the series takes the mean of four quarter-hour prices, as an
    hourly contract bills it; a quarter hour takes the price of its hour. A
    slot only partly covered keeps its forecast rather than a guess.
    """
    covered = 0.0
    total = 0.0
    for slot in slots:
        if slot.end <= start or slot.start >= end:
            continue
        overlap = (min(slot.end, end) - max(slot.start, start)).total_seconds()
        covered += overlap
        total += slot.value * overlap
    length = (end - start).total_seconds()
    if length <= 0 or covered + 1 < length:
        return None
    return total / covered


def _entry_bounds(entry: Any) -> tuple[datetime, datetime] | None:
    if not isinstance(entry, dict):
        return None
    start = _moment(entry.get("start"))
    end = _moment(entry.get("end"))
    if start is None or end is None or end <= start:
        return None
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def merge(
    series: dict[str, Any] | None,
    slots: Sequence[OwnSlot],
    *,
    now: datetime,
    zone: tzinfo,
    step_minutes: int,
    currency: str | None = None,
) -> tuple[dict[str, Any] | None, MergeReport]:
    """Put the source's prices into the forecast slots they cover.

    * A forecast slot fully covered by the source takes its price and the
      source OWN_SOURCE; a possible-spike mark goes with the forecast.
    * A published slot of the API stays as it is. Both come from the same
      auction, so they should agree; where they differ by more than half a
      euro cent the report says so.
    * Where the API has no slot at all on today or tomorrow - the hours of
      today already past, in a zone where the API only has a forecast - the
      source fills in, at the series' own resolution.
    """
    report = MergeReport()
    if not slots or not isinstance(series, dict):
        return series, report
    entries = series.get("entries")
    if not isinstance(entries, list):
        return series, report

    limit = tolerance(currency)
    merged: list[Any] = []
    taken: list[tuple[datetime, datetime]] = []
    for entry in entries:
        bounds = _entry_bounds(entry)
        if bounds is None:
            merged.append(entry)
            continue
        taken.append(bounds)
        own = _covered_average(slots, *bounds)
        if own is None:
            merged.append(entry)
            continue
        if entry.get("source") == "forecast":
            replaced = {
                key: value for key, value in entry.items() if key != "spike_possible"
            }
            replaced.update(value=round(own, 6), source=OWN_SOURCE)
            merged.append(replaced)
            report.replaced += 1
            continue
        merged.append(entry)
        published = _number(entry.get("value"))
        if published is None:
            continue
        report.compared += 1
        if abs(own - published) > limit:
            report.disagreement_count += 1
            if len(report.disagreements) < _MAX_LISTED:
                report.disagreements.append(
                    {
                        "start": _iso(bounds[0]),
                        "published": published,
                        "own_source": round(own, 6),
                    }
                )

    # Fill where the API has nothing, on today and tomorrow only - the days
    # the source can know - and at the series' resolution.
    step = timedelta(minutes=step_minutes)
    local_midnight = now.astimezone(zone).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    first_day = local_midnight.astimezone(timezone.utc)
    # Aware arithmetic keeps the wall clock, so this is local midnight two
    # days on even across a daylight-saving change.
    day_after = (local_midnight + timedelta(days=2)).astimezone(timezone.utc)
    begin = max(first_day, slots[0].start)
    finish = min(day_after, slots[-1].end)
    cursor = first_day
    while cursor < begin:
        cursor += step
    while cursor + step <= finish:
        end = cursor + step
        if not any(a < end and b > cursor for a, b in taken):
            own = _covered_average(slots, cursor, end)
            if own is not None:
                merged.append(
                    {
                        "start": _iso(cursor),
                        "end": _iso(end),
                        "value": round(own, 6),
                        "source": OWN_SOURCE,
                    }
                )
                report.added += 1
        cursor = end

    if report.added:
        merged.sort(
            key=lambda entry: (
                (0, bounds[0].timestamp())
                if (bounds := _entry_bounds(entry)) is not None
                else (1, 0.0)
            )
        )
    return {**series, "entries": merged}, report


def level_check(
    slots: Sequence[OwnSlot],
    entries: Any,
    currency: str | None = None,
) -> str | None:
    """Whether the source looks like it carries charges, VAT or a wrong area.

    Run by the settings form before it saves. Against published prices the
    answer is exact: the same auction gives the same number. Against a
    forecast only a large, steady offset counts. None when the prices agree
    or there is too little overlap to judge - then nothing is held against
    the source.

    Returns "published_mismatch" or "forecast_mismatch".
    """
    if not slots or not isinstance(entries, list):
        return None
    published: list[tuple[float, float]] = []
    forecast: list[tuple[float, float]] = []
    for entry in entries:
        bounds = _entry_bounds(entry)
        value = _number(entry.get("value")) if isinstance(entry, dict) else None
        if bounds is None or value is None:
            continue
        own = _covered_average(slots, *bounds)
        if own is None:
            continue
        if entry.get("source") == "forecast":
            forecast.append((own, value))
        else:
            published.append((own, value))

    limit = tolerance(currency)
    if len(published) >= _MIN_PAIRS:
        apart = sum(1 for own, api in published if abs(own - api) > limit)
        return "published_mismatch" if apart * 2 > len(published) else None
    if len(forecast) >= _MIN_PAIRS:
        offset = median(own - api for own, api in forecast)
        level = median(abs(api) for _own, api in forecast)
        allowed = max(
            limit * _FORECAST_TOLERANCE_FACTOR, level * _FORECAST_TOLERANCE_SHARE
        )
        return "forecast_mismatch" if abs(offset) > allowed else None
    return None


def signature(slots: Sequence[OwnSlot]) -> tuple[Any, ...]:
    """A cheap fingerprint, so an unchanged source triggers no rebuild."""
    if not slots:
        return ()
    return (
        len(slots),
        slots[0].start,
        slots[-1].end,
        round(sum(slot.value for slot in slots), 6),
    )


def series_currency(unit: Any) -> str | None:
    """The currency of the API series, from its unit such as "EUR/kWh"."""
    parsed = parse_unit(unit)
    return parsed[0] if parsed else None
