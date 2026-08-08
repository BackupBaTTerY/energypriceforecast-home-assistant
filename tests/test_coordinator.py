"""Tests for the cheapest-hours bucketing logic in the coordinator.

The API only ever finds the single best *contiguous* window. The
integration additionally supports picking the N cheapest, possibly
non-contiguous, calendar hours from the raw price series - this is the
common "run the washing machine during the cheapest hours" automation
pattern.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.energypriceforecast.coordinator import _cheapest_hour_blocks


def _now_hour() -> datetime:
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _quarter_hour_entries(prices_by_hour: dict[int, float]) -> list[dict]:
    """Build four 15-minute entries per hour, all sharing the hour's price."""
    now = _now_hour()
    entries = []
    for hour_offset, price in prices_by_hour.items():
        for quarter in range(4):
            start = now + timedelta(hours=hour_offset, minutes=quarter * 15)
            entries.append({"start": start.isoformat(), "value": price})
    return entries


def test_picks_the_n_cheapest_non_contiguous_hours() -> None:
    entries = _quarter_hour_entries({0: 0.30, 1: 0.10, 2: 0.50, 3: 0.05, 4: 0.20})

    result = _cheapest_hour_blocks(entries, count=2)

    now = _now_hour()
    assert [hour["start"] for hour in result] == [
        now + timedelta(hours=1),
        now + timedelta(hours=3),
    ]


def test_excludes_hours_that_have_already_passed() -> None:
    now = _now_hour()
    entries = [
        {"start": (now - timedelta(hours=1)).isoformat(), "value": 0.001},
        {"start": now.isoformat(), "value": 0.5},
    ]

    result = _cheapest_hour_blocks(entries, count=5)

    assert len(result) == 1
    assert result[0]["start"] == now


def test_zero_count_selects_nothing() -> None:
    entries = _quarter_hour_entries({0: 0.1})

    assert _cheapest_hour_blocks(entries, count=0) == []


def test_averages_multiple_slots_within_the_same_hour() -> None:
    now = _now_hour()
    entries = [
        {"start": now.isoformat(), "value": 0.10},
        {"start": (now + timedelta(minutes=30)).isoformat(), "value": 0.30},
    ]

    result = _cheapest_hour_blocks(entries, count=1)

    assert result[0]["average_value"] == 0.20


def test_ignores_entries_with_missing_or_invalid_fields() -> None:
    now = _now_hour()
    entries = [
        {"start": now.isoformat(), "value": 0.1},
        {"start": now.isoformat()},  # missing value
        {"value": 0.05},  # missing start
        {"start": "not-a-timestamp", "value": 0.01},
    ]

    result = _cheapest_hour_blocks(entries, count=5)

    assert len(result) == 1
    assert result[0]["average_value"] == 0.1
