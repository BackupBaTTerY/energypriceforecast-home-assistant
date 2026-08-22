"""Tests for the cheapest-hours bucketing logic in the coordinator.

The API only ever finds the single best *contiguous* window. The
integration additionally supports picking the N cheapest, possibly
non-contiguous, calendar hours from the raw price series - this is the
common "run the washing machine during the cheapest hours" automation
pattern. Selection happens independently per local calendar day, so a
recurring automation actually recurs every day instead of an N-hour
budget landing entirely on whichever day happens to be cheaper overall.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.energypriceforecast.coordinator import _cheapest_hour_blocks

# Fixed reference "now", away from any UTC midnight boundary, so hour
# offsets used across these tests land on the day the test expects.
_NOW = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)


def _quarter_hour_entries(prices_by_hour: dict[int, float]) -> list[dict]:
    """Build four 15-minute entries per hour, all sharing the hour's price."""
    entries = []
    for hour_offset, price in prices_by_hour.items():
        for quarter in range(4):
            start = _NOW + timedelta(hours=hour_offset, minutes=quarter * 15)
            entries.append({"start": start.isoformat(), "value": price})
    return entries


def test_picks_the_n_cheapest_non_contiguous_hours(freezer) -> None:
    freezer.move_to(_NOW)
    entries = _quarter_hour_entries({0: 0.30, 1: 0.10, 2: 0.50, 3: 0.05, 4: 0.20})

    result = _cheapest_hour_blocks(entries, count=2)

    assert [hour["start"] for hour in result] == [
        _NOW + timedelta(hours=1),
        _NOW + timedelta(hours=3),
    ]


def test_excludes_hours_that_have_already_passed(freezer) -> None:
    freezer.move_to(_NOW)
    entries = [
        {"start": (_NOW - timedelta(hours=1)).isoformat(), "value": 0.001},
        {"start": _NOW.isoformat(), "value": 0.5},
    ]

    result = _cheapest_hour_blocks(entries, count=5)

    assert len(result) == 1
    assert result[0]["start"] == _NOW


def test_zero_count_selects_nothing(freezer) -> None:
    freezer.move_to(_NOW)
    entries = _quarter_hour_entries({0: 0.1})

    assert _cheapest_hour_blocks(entries, count=0) == []


def test_averages_multiple_slots_within_the_same_hour(freezer) -> None:
    freezer.move_to(_NOW)
    entries = [
        {"start": _NOW.isoformat(), "value": 0.10},
        {"start": (_NOW + timedelta(minutes=30)).isoformat(), "value": 0.30},
    ]

    result = _cheapest_hour_blocks(entries, count=1)

    assert result[0]["average_value"] == 0.20


def test_ignores_entries_with_missing_or_invalid_fields(freezer) -> None:
    freezer.move_to(_NOW)
    entries = [
        {"start": _NOW.isoformat(), "value": 0.1},
        {"start": _NOW.isoformat()},  # missing value
        {"value": 0.05},  # missing start
        {"start": "not-a-timestamp", "value": 0.01},
    ]

    result = _cheapest_hour_blocks(entries, count=5)

    assert len(result) == 1
    assert result[0]["average_value"] == 0.1


def test_selects_cheapest_hours_separately_for_each_calendar_day(freezer) -> None:
    """A count of 2 must yield 2 cheap hours on *each* day, not 2 total.

    Today has one very cheap hour and otherwise expensive hours; tomorrow
    is uniformly cheaper overall. A whole-horizon top-N would put both
    picks on tomorrow, leaving today without any cheap-hours window at
    all - exactly the behaviour this per-day split fixes.
    """
    freezer.move_to(_NOW)
    entries = _quarter_hour_entries(
        {
            0: 0.05,  # today, very cheap
            1: 0.40,
            2: 0.40,
            24: 0.10,  # tomorrow, cheapest
            25: 0.11,  # tomorrow, second cheapest
            26: 0.12,
        }
    )

    result = _cheapest_hour_blocks(entries, count=2)

    assert [hour["start"] for hour in result] == [
        _NOW + timedelta(hours=0),
        _NOW + timedelta(hours=1),
        _NOW + timedelta(hours=24),
        _NOW + timedelta(hours=25),
    ]


def test_fewer_available_hours_than_count_still_returns_that_days_hours(
    freezer,
) -> None:
    """If a day only has 1 hour left, a count of 3 returns just that 1."""
    freezer.move_to(_NOW)
    entries = _quarter_hour_entries({0: 0.20, 24: 0.10, 25: 0.30, 26: 0.05})

    result = _cheapest_hour_blocks(entries, count=3)

    starts = [hour["start"] for hour in result]
    assert _NOW + timedelta(hours=0) in starts
    assert len([s for s in starts if s == _NOW]) == 1
    # tomorrow has 3 hours available, all 3 fit within count=3
    assert _NOW + timedelta(hours=24) in starts
    assert _NOW + timedelta(hours=25) in starts
    assert _NOW + timedelta(hours=26) in starts
    assert len(starts) == 4
