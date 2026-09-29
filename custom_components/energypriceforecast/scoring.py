"""Where the present stands among the hours ahead, on price and CO2 together.

The combined window the API reports is the best compromise somewhere in the
horizon, and its old score said how far the cheapest and the cleanest windows
disagree - which in a solar market they almost never do, so it sat at 100.
This answers the question an automation can act on instead: how good is the
present, compared with the rest of the coming day?

Three choices shape the number, and each was measured before it was made:

- The reference is the next 24 hours, but only as far as prices are
  published. The frozen forecast for tomorrow's early hours ran 1.5 to 4.3
  ct/kWh too low in 11 of 12 markets over 30 days; ranked against it, every
  morning looked worse than it was, by 18 to 24 points at noon in Norway.
- Every slot gets a standing: how many typical spreads its price and its CO2
  intensity lie below the reference's median, added up. The spread runs from
  the 10th to the 90th percentile, so one spike cannot set the scale, and it
  has a floor, so a quantity that barely moves barely counts - a hydro grid
  at 20-21 g all day would otherwise swing on a tenth of a gram.
- The score is the rank of the present's standing: the share of the other
  reference slots that stand worse, ties counting half. Averaging a price
  rank and a CO2 rank instead amplified that same noise, and squeezed days on
  which cheap and clean disagree towards 50, where no threshold ever fires.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .const import (
    COMBINED_SCORE_CO2_FLOOR_G_KWH,
    COMBINED_SCORE_MIN_REFERENCE_HOURS,
    COMBINED_SCORE_PRICE_FLOOR_SHARE,
    COMBINED_SCORE_REFERENCE_HOURS,
)
from .planning import _parse_entries

# Standings closer than this are the same standing.
_TIE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class CombinedScore:
    """The score for the present slot, and the numbers it was built from."""

    score: float
    price_part: float
    co2_part: float | None
    co2_share: float | None
    price_spread: float
    co2_spread: float | None
    reference_start: datetime
    reference_end: datetime
    reference_slots: int


@dataclass(frozen=True)
class CombinedScorePoint:
    """One slot ranked inside a shared combined-score reference."""

    start: datetime
    end: datetime
    score: float
    price_part: float
    co2_part: float | None


@dataclass(frozen=True)
class CombinedScoreSeries:
    """Comparable scores for every slot in one fixed reference block."""

    points: tuple[CombinedScorePoint, ...]
    co2_share: float | None
    price_spread: float
    co2_spread: float | None
    reference_start: datetime
    reference_end: datetime


def _quantile(values: list[float], fraction: float) -> float:
    """Linearly interpolated quantile of a non-empty list."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _variance(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _parts(values: list[float], floor: float) -> tuple[list[float], float]:
    """Each value's distance below the median, measured in typical spreads."""
    median = _quantile(values, 0.5)
    spread = max(_quantile(values, 0.9) - _quantile(values, 0.1), floor)
    if spread <= 0:
        # Every value equal and no floor to fall back on: nothing to measure.
        return [0.0] * len(values), 0.0
    return [(median - value) / spread for value in values], spread


def _rank(standings: list[float], index: int) -> float:
    """Rank one standing against every other standing in the same block."""
    selected = standings[index]
    others = [value for other_index, value in enumerate(standings) if other_index != index]
    worse = sum(1 for standing in others if standing < selected - _TIE_TOLERANCE)
    ties = sum(1 for standing in others if abs(standing - selected) <= _TIE_TOLERANCE)
    return 100 * (worse + 0.5 * ties) / len(others)


def combined_score_series(
    price_entries: list[dict[str, Any]] | None,
    co2_entries: list[dict[str, Any]] | None,
    now: datetime,
) -> CombinedScoreSeries | None:
    """Rank every upcoming published slot inside one shared reference.

    The first point is the slot covering ``now``. Every point uses the same
    medians, spreads and competing slots, so the resulting line can be read as
    an outlook and its future values can be compared directly. Forecast prices
    stay out for the same measured reason as in ``combined_score_now``.
    """
    published = [item for item in _parse_entries(price_entries or []) if item[3]]
    current = next((item for item in published if item[0] <= now < item[1]), None)
    if current is None:
        return None

    horizon_end = current[0] + timedelta(hours=COMBINED_SCORE_REFERENCE_HOURS)
    reference = [current]
    for item in published:
        if item[0] <= current[0]:
            continue
        # Published day-ahead prices are contiguous. Should a gap appear, stop
        # at it rather than rank against a reference that is not what it says.
        if item[0] >= horizon_end or item[0] != reference[-1][1]:
            break
        reference.append(item)
    reference_start = reference[0][0]
    reference_end = min(reference[-1][1], horizon_end)
    if reference_end - reference_start < timedelta(
        hours=COMBINED_SCORE_MIN_REFERENCE_HOURS
    ) or len(reference) < 2:
        return None

    prices = [item[2] for item in reference]
    price_floor = COMBINED_SCORE_PRICE_FLOOR_SHARE * (
        sum(abs(price) for price in prices) / len(prices)
    )
    price_parts, price_spread = _parts(prices, price_floor)

    co2_slots = _parse_entries(co2_entries or [])
    co2_values = [
        next(
            (
                value
                for start, end, value, _settled in co2_slots
                if start <= item[0] < end
            ),
            None,
        )
        for item in reference
    ]
    co2_parts: list[float] | None = None
    co2_spread: float | None = None
    if all(value is not None for value in co2_values):
        co2_parts, co2_spread = _parts(
            [value for value in co2_values if value is not None],
            COMBINED_SCORE_CO2_FLOOR_G_KWH,
        )

    standings = (
        [price + co2 for price, co2 in zip(price_parts, co2_parts)]
        if co2_parts is not None
        else price_parts
    )
    co2_share: float | None = None
    if co2_parts is not None:
        total = _variance(price_parts) + _variance(co2_parts)
        co2_share = _variance(co2_parts) / total if total > 0 else None

    points = tuple(
        CombinedScorePoint(
            start=item[0],
            end=min(item[1], reference_end),
            score=_rank(standings, index),
            price_part=price_parts[index],
            co2_part=None if co2_parts is None else co2_parts[index],
        )
        for index, item in enumerate(reference)
    )
    return CombinedScoreSeries(
        points=points,
        co2_share=co2_share,
        price_spread=price_spread,
        co2_spread=co2_spread,
        reference_start=reference_start,
        reference_end=reference_end,
    )


def combined_score_now(
    price_entries: list[dict[str, Any]] | None,
    co2_entries: list[dict[str, Any]] | None,
    now: datetime,
) -> CombinedScore | None:
    """Score the slot covering ``now`` against the published slots ahead.

    None when the present has no published price, or when published prices
    reach fewer than COMBINED_SCORE_MIN_REFERENCE_HOURS ahead. CO2 counts only
    if it covers every slot of the reference; otherwise this is a price score
    and says so with ``co2_part`` None, rather than ranking some slots on two
    counts and the rest on one.
    """
    series = combined_score_series(price_entries, co2_entries, now)
    if series is None:
        return None
    present = series.points[0]
    return CombinedScore(
        score=present.score,
        price_part=present.price_part,
        co2_part=present.co2_part,
        co2_share=series.co2_share,
        price_spread=series.price_spread,
        co2_spread=series.co2_spread,
        reference_start=series.reference_start,
        reference_end=series.reference_end,
        reference_slots=len(series.points),
    )
