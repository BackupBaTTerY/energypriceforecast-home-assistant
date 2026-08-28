"""Fixed, repeating time blocks and locked cheapest-hour plans.

Two related but independent concepts live here:

- A *block* is a span of wall-clock time (e.g. "every 24 hours starting at
  local midnight", or "every Saturday 00:00 to Monday 00:00"). Block
  boundaries are a pure function of the current time and the block's own
  parameters - the same inputs always produce the same boundaries, which
  is what makes a plan for that block cacheable by its start time.
- A *plan* is the set of cheapest individual hours picked inside one block.
  Once a plan has been computed for a given block, callers are expected to
  cache and reuse it (see coordinator.py) rather than recomputing on every
  poll - otherwise a later forecast revision could reshuffle which hours
  are "cheapest" mid-block, which defeats the point of a recurring
  automation like "run the heat pump during the cheapest hours every day".
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


def fixed_repeating_window(
    now: datetime, start_hour: int, window_hours: int
) -> tuple[datetime, datetime]:
    """Return the [start, end) of the fixed, repeating block containing now.

    Blocks repeat every window_hours, anchored to local midnight plus
    start_hour on an arbitrary fixed reference Monday. Anchoring to wall
    clock time (not a fixed real-world duration since some reference
    instant) means a block always starts at the same local time of day
    even across a DST change, matching what a user actually expects from
    "every day" or "every 24 hours starting at midnight".
    """
    local_now = dt_util.as_local(now)
    naive_now = local_now.replace(tzinfo=None)
    reference = datetime(2024, 1, 1) + timedelta(hours=start_hour)  # a Monday
    block_index = (naive_now - reference) // timedelta(hours=window_hours)
    naive_start = reference + block_index * timedelta(hours=window_hours)
    naive_end = naive_start + timedelta(hours=window_hours)
    local_tz = local_now.tzinfo
    return (
        dt_util.as_utc(naive_start.replace(tzinfo=local_tz)),
        dt_util.as_utc(naive_end.replace(tzinfo=local_tz)),
    )


def fixed_weekend_window(now: datetime) -> tuple[datetime, datetime]:
    """Return the current-or-next Saturday 00:00 - Monday 00:00 local block.

    While now falls on a Saturday or Sunday, this is the active weekend
    block. On any other day it is the upcoming weekend - callers that only
    care about "is it active right now" should still check
    ``start <= now < end`` themselves.
    """
    local_now = dt_util.as_local(now)
    naive_now = local_now.replace(tzinfo=None)
    days_since_saturday = (naive_now.weekday() - 5) % 7
    naive_start = datetime.combine(
        naive_now.date() - timedelta(days=days_since_saturday), time()
    )
    naive_end = naive_start + timedelta(hours=48)
    if naive_now >= naive_end:
        naive_start += timedelta(days=7)
        naive_end += timedelta(days=7)
    local_tz = local_now.tzinfo
    return (
        dt_util.as_utc(naive_start.replace(tzinfo=local_tz)),
        dt_util.as_utc(naive_end.replace(tzinfo=local_tz)),
    )


def _parse_entries(
    entries: list[dict[str, Any]],
) -> list[tuple[datetime, datetime, float]]:
    parsed: list[tuple[datetime, datetime, float]] = []
    for entry in entries:
        start_raw = entry.get("start")
        value = entry.get("value")
        if not isinstance(start_raw, str) or not isinstance(value, (int, float)):
            continue
        try:
            start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        end_raw = entry.get("end")
        end = None
        if isinstance(end_raw, str):
            try:
                end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            except ValueError:
                end = None
        if end is None or end <= start:
            end = start + timedelta(minutes=15)
        parsed.append((start, end, float(value)))
    parsed.sort(key=lambda item: item[0])
    return parsed


def _integrated_average(
    entries: list[tuple[datetime, datetime, float]], start: datetime, end: datetime
) -> float | None:
    """Duration-weighted average price over [start, end).

    Returns None if any part of the range is not covered by an entry -
    an average built from a gap would silently understate or overstate
    the real price for that stretch.
    """
    if end <= start:
        return None
    cursor = start
    weighted = 0.0
    covered = timedelta()
    for entry_start, entry_end, value in entries:
        if entry_end <= cursor or entry_start >= end:
            continue
        if entry_start > cursor:
            return None
        overlap_start = max(cursor, entry_start)
        overlap_end = min(end, entry_end)
        if overlap_end <= overlap_start:
            continue
        duration = overlap_end - overlap_start
        weighted += value * duration.total_seconds()
        covered += duration
        cursor = overlap_end
        if cursor >= end:
            break
    if cursor >= end and covered == end - start:
        return weighted / covered.total_seconds()
    return None


def select_cheapest_hours(
    entries: list[dict[str, Any]],
    count: int,
    window_start: datetime,
    window_end: datetime,
    available_from: datetime,
) -> list[dict[str, Any]] | None:
    """Pick the count cheapest whole hours inside [window_start, window_end).

    Returns None - no plan - if any hour from available_from onward lacks
    full price coverage. Publishing a partial plan (or silently picking
    from whatever happens to be available) would be misleading for an
    automation that expects a stable, complete answer.
    """
    if count < 1 or window_end <= window_start:
        return None

    parsed = _parse_entries(entries)
    hour = timedelta(hours=1)
    first_hour_start = max(
        window_start, available_from.replace(minute=0, second=0, microsecond=0)
    )

    hours: list[dict[str, Any]] = []
    hour_start = first_hour_start
    while hour_start < window_end:
        hour_end = min(hour_start + hour, window_end)
        covered_start = max(hour_start, available_from)
        average = _integrated_average(parsed, covered_start, hour_end)
        if average is None:
            return None
        hours.append(
            {"start": hour_start, "end": hour_end, "average_value": average}
        )
        hour_start += hour

    cheapest = sorted(hours, key=lambda h: h["average_value"])[:count]
    return sorted(cheapest, key=lambda h: h["start"])
