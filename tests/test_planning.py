"""Tests for fixed time blocks and locked cheapest-hour plans."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.util import dt as dt_util

from custom_components.energypriceforecast.planning import (
    fixed_repeating_window,
    fixed_weekend_window,
    select_cheapest_hours,
    window_is_settled,
)


@pytest.fixture(autouse=True)
def _utc_local_time_zone():
    """Pin dt_util's local time zone to UTC for these tests.

    fixed_repeating_window/fixed_weekend_window are deliberately local-time
    aware (see planning.py), so these tests compare against UTC-based
    expectations under the assumption that "local" is UTC. Without pinning
    it, these plain unit tests (no hass fixture) inherit whatever the
    process-global dt_util default time zone happens to be left at by
    other test modules' hass fixtures (pytest-homeassistant-custom-component
    defaults it to US/Pacific), which is a test-order-dependent leak, not a
    bug in the code under test.
    """
    original = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(dt_util.UTC)
    yield
    dt_util.set_default_time_zone(original)


def _quarter_hour_entries(
    start: datetime, prices_by_hour: dict[int, float], source: str | None = None
) -> list[dict]:
    entries = []
    for hour_offset, price in prices_by_hour.items():
        for quarter in range(4):
            slot_start = start + timedelta(hours=hour_offset, minutes=quarter * 15)
            entry = {
                "start": slot_start.isoformat(),
                "end": (slot_start + timedelta(minutes=15)).isoformat(),
                "value": price,
            }
            if source is not None:
                entry["source"] = source
            entries.append(entry)
    return entries


def _quarter_prices(start: datetime, hour_offset: int, prices: list[float]) -> list[dict]:
    """Four quarter-hour entries with individual prices, for one hour."""
    entries = []
    for quarter, price in enumerate(prices):
        slot_start = start + timedelta(hours=hour_offset, minutes=quarter * 15)
        entries.append(
            {
                "start": slot_start.isoformat(),
                "end": (slot_start + timedelta(minutes=15)).isoformat(),
                "value": price,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# fixed_repeating_window
# ---------------------------------------------------------------------------


def test_daily_block_starts_at_local_midnight() -> None:
    now = datetime(2026, 8, 12, 14, 30, tzinfo=timezone.utc)

    start, end = fixed_repeating_window(now, start_hour=0, window_hours=24)

    assert start == datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)


def test_daily_block_respects_a_non_midnight_anchor() -> None:
    now = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)

    start, end = fixed_repeating_window(now, start_hour=6, window_hours=24)

    # now is before 06:00, so we're still inside the block that started
    # the previous day at 06:00.
    assert start == datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 12, 6, 0, tzinfo=timezone.utc)


def test_48_hour_block_spans_two_days_and_is_stable() -> None:
    reference = datetime(2024, 1, 1, tzinfo=timezone.utc)  # a Monday
    now_a = reference + timedelta(hours=10)
    now_b = reference + timedelta(hours=30)

    window_a = fixed_repeating_window(now_a, start_hour=0, window_hours=48)
    window_b = fixed_repeating_window(now_b, start_hour=0, window_hours=48)

    assert window_a == window_b == (reference, reference + timedelta(hours=48))


def test_block_boundaries_are_a_pure_function_of_now() -> None:
    """Calling it twice for points in the same block must return the same block."""
    now_a = datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)
    now_b = datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)

    assert fixed_repeating_window(now_a, 0, 24) == fixed_repeating_window(now_b, 0, 24)


# ---------------------------------------------------------------------------
# fixed_weekend_window
# ---------------------------------------------------------------------------


def test_weekend_window_on_saturday_is_the_current_weekend() -> None:
    saturday_noon = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)  # a Saturday

    start, end = fixed_weekend_window(saturday_noon)

    assert start == datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    assert start <= saturday_noon < end


def test_weekend_window_on_a_weekday_is_the_upcoming_weekend() -> None:
    wednesday = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)

    start, end = fixed_weekend_window(wednesday)

    assert start == datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    assert not (start <= wednesday < end)


def test_weekend_window_at_the_monday_boundary_has_just_ended() -> None:
    monday_midnight = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)

    start, end = fixed_weekend_window(monday_midnight)

    # The weekend that just ended is no longer "current"; the function
    # must return the *next* one, seven days later.
    assert start == datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# select_cheapest_hours
# ---------------------------------------------------------------------------


def test_selects_the_cheapest_hours_within_the_window() -> None:
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    entries = _quarter_hour_entries(
        start, {0: 0.30, 1: 0.10, 2: 0.50, 3: 0.05, 4: 0.20}
    )
    # fill the rest of the day so the window has full coverage
    for hour in range(5, 24):
        entries.extend(_quarter_hour_entries(start, {hour: 0.99}))

    result = select_cheapest_hours(entries, count=2, window_start=start, window_end=end, available_from=start)

    assert result is not None
    assert [h["start"] for h in result["hours"]] == [
        start + timedelta(hours=1),
        start + timedelta(hours=3),
    ]


def test_returns_none_when_the_window_is_not_fully_covered() -> None:
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=24)
    # Only the first two hours have data; the rest of the day is missing.
    entries = _quarter_hour_entries(start, {0: 0.1, 1: 0.2})

    result = select_cheapest_hours(entries, count=2, window_start=start, window_end=end, available_from=start)

    assert result is None


def test_only_requires_coverage_from_available_from_onward() -> None:
    """Hours before available_from (already passed) don't need data."""
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=4)
    available_from = start + timedelta(hours=2)
    # No data at all for hours 0-1 (already past), full data for 2-3.
    entries = _quarter_hour_entries(start, {2: 0.10, 3: 0.20})

    result = select_cheapest_hours(
        entries, count=5, window_start=start, window_end=end, available_from=available_from
    )

    assert result is not None
    assert [h["start"] for h in result["hours"]] == [
        start + timedelta(hours=2),
        start + timedelta(hours=3),
    ]


def test_plan_reports_the_average_of_the_whole_block() -> None:
    """The baseline covers every hour of the block, not just the picks.

    Comparing the plan against itself would always show no saving, so the
    figure the saving sensor divides by has to be the block as a whole.
    """
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=4)
    entries = _quarter_hour_entries(start, {0: 0.10, 1: 0.20, 2: 0.30, 3: 0.40})

    result = select_cheapest_hours(
        entries, count=2, window_start=start, window_end=end, available_from=start
    )

    assert result is not None
    assert [h["average_value"] for h in result["hours"]] == [0.10, 0.20]
    # (0.10 + 0.20 + 0.30 + 0.40) / 4
    assert result["window_average_value"] == pytest.approx(0.25)


def test_block_average_weights_a_clipped_final_hour() -> None:
    """A block ending mid-hour must not count that stub as a full hour."""
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=2, minutes=30)
    entries = _quarter_hour_entries(start, {0: 0.10, 1: 0.20, 2: 0.60})

    result = select_cheapest_hours(
        entries, count=1, window_start=start, window_end=end, available_from=start
    )

    assert result is not None
    # 0.10 and 0.20 last a full hour, 0.60 only half of one:
    # (0.10*60 + 0.20*60 + 0.60*30) / 150
    assert result["window_average_value"] == pytest.approx(0.24)


def test_an_hour_the_block_is_already_inside_is_cut_back_to_what_is_left() -> None:
    """A planned hour must never start before the moment it was planned.

    A plan locked at 02:30 used to report the hour it was standing in as a
    full 02:00-03:00 hour, so the chart drew a planned band 30 minutes into
    the past and an automation saw a start time that had already gone by.
    """
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=4)
    available_from = start + timedelta(hours=2, minutes=30)
    entries = _quarter_hour_entries(start, {2: 0.10, 3: 0.20})

    result = select_cheapest_hours(
        entries, count=1, window_start=start, window_end=end,
        available_from=available_from,
    )

    assert result is not None
    assert result["hours"][0]["start"] == available_from
    assert result["hours"][0]["end"] == start + timedelta(hours=3)


def test_a_started_hour_is_priced_on_the_minutes_that_are_left() -> None:
    """Its price must describe the part still ahead, and say so in its span.

    The old code took the average of the remaining minutes but kept the full
    60-minute edge, which let a quarter of an hour compete for a slot as if
    a whole cheap hour were on offer.
    """
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=4)
    available_from = start + timedelta(hours=2, minutes=30)
    entries = (
        _quarter_prices(start, 2, [0.90, 0.90, 0.01, 0.01])
        + _quarter_hour_entries(start, {3: 0.20})
    )

    result = select_cheapest_hours(
        entries, count=1, window_start=start, window_end=end,
        available_from=available_from,
    )

    assert result is not None
    picked = result["hours"][0]
    assert picked["average_value"] == pytest.approx(0.01)
    # Half an hour of cheap power, and reported as half an hour.
    assert picked["end"] - picked["start"] == timedelta(minutes=30)


def test_block_average_weights_a_started_first_hour() -> None:
    """The stub at the front of a block must not count as a whole hour."""
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)
    available_from = start + timedelta(minutes=30)
    entries = _quarter_hour_entries(start, {0: 0.10, 1: 0.20, 2: 0.60})

    result = select_cheapest_hours(
        entries, count=1, window_start=start, window_end=end,
        available_from=available_from,
    )

    assert result is not None
    # (0.10*30 + 0.20*60 + 0.60*60) / 150 - not /180, which is what counting
    # the half hour as a full one would give.
    assert result["window_average_value"] == pytest.approx(0.34)


# ---------------------------------------------------------------------------
# window_is_settled
# ---------------------------------------------------------------------------


def test_a_window_of_published_prices_is_settled() -> None:
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    entries = _quarter_hour_entries(start, {0: 0.10, 1: 0.20}, source="day_ahead")

    assert window_is_settled(entries, start, start + timedelta(hours=2)) is True


def test_a_window_holding_any_forecast_is_not_settled() -> None:
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    entries = _quarter_hour_entries(
        start, {0: 0.10}, source="day_ahead"
    ) + _quarter_hour_entries(start, {1: 0.20}, source="forecast")

    assert window_is_settled(entries, start, start + timedelta(hours=2)) is False
    # The published part on its own still is.
    assert window_is_settled(entries, start, start + timedelta(hours=1)) is True


def test_an_entry_without_a_source_never_counts_as_settled() -> None:
    """An unfamiliar payload must not be able to freeze a plan by accident."""
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    entries = _quarter_hour_entries(start, {0: 0.10, 1: 0.20})

    assert window_is_settled(entries, start, start + timedelta(hours=2)) is False


def test_a_gap_in_the_published_prices_is_not_settled() -> None:
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    entries = _quarter_hour_entries(start, {0: 0.10, 2: 0.20}, source="day_ahead")

    assert window_is_settled(entries, start, start + timedelta(hours=3)) is False


def test_an_empty_range_is_not_settled() -> None:
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    entries = _quarter_hour_entries(start, {0: 0.10}, source="day_ahead")

    assert window_is_settled(entries, start, start) is False


def test_zero_or_negative_count_yields_no_plan() -> None:
    start = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    entries = _quarter_hour_entries(start, {0: 0.1})

    assert select_cheapest_hours(entries, count=0, window_start=start, window_end=end, available_from=start) is None
