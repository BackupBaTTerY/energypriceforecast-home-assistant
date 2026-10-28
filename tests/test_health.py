"""Continuity and freshness checks behind the diagnostics.

Pure Python, so these run without the Home Assistant test plugin.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Loaded by path: importing the package would pull in Home Assistant, which
# this module does not need.
_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "energypriceforecast"
    / "health.py"
)
_SPEC = importlib.util.spec_from_file_location("epf_health", _PATH)
health = importlib.util.module_from_spec(_SPEC)
sys.modules["epf_health"] = health
_SPEC.loader.exec_module(health)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _local_day(day: datetime, step_minutes: int = 60) -> list[dict]:
    """Every slot of one local market day, built in UTC as the API sends it."""
    start = day.replace(tzinfo=AMSTERDAM).astimezone(timezone.utc)
    end = (day + timedelta(days=1)).replace(tzinfo=AMSTERDAM).astimezone(timezone.utc)
    slots = []
    moment = start
    while moment < end:
        nxt = moment + timedelta(minutes=step_minutes)
        slots.append(
            {
                "start": moment.isoformat().replace("+00:00", "Z"),
                "end": nxt.isoformat().replace("+00:00", "Z"),
                "value": 0.1,
            }
        )
        moment = nxt
    return slots


def test_the_autumn_change_day_has_25_joined_hours() -> None:
    day = _local_day(datetime(2026, 10, 25))
    report = health.series_continuity(day)
    assert report["slots"] == 25
    assert report["gaps"] == 0
    assert report["overlaps"] == 0
    assert report["first_start"] == "2026-10-24T22:00:00Z"
    assert report["last_end"] == "2026-10-25T23:00:00Z"


def test_the_spring_change_day_has_23_joined_hours() -> None:
    day = _local_day(datetime(2027, 3, 28))
    report = health.series_continuity(day)
    assert report["slots"] == 23
    assert report["gaps"] == 0
    assert report["overlaps"] == 0


def test_quarter_hours_across_the_autumn_change_join_up() -> None:
    report = health.series_continuity(_local_day(datetime(2026, 10, 25), 15))
    assert report["slots"] == 100
    assert report["gaps"] == 0


def test_a_lost_repeated_hour_is_named_as_a_gap() -> None:
    """What issue #6 reported: the second 02:00 (01:00 UTC) went missing."""
    day = [
        slot
        for slot in _local_day(datetime(2026, 10, 25))
        if slot["start"] != "2026-10-25T01:00:00Z"
    ]
    report = health.series_continuity(day)
    assert report["slots"] == 24
    assert report["gaps"] == 1
    assert report["gap_details"] == [
        {
            "after": "2026-10-25T01:00:00Z",
            "before": "2026-10-25T02:00:00Z",
            "missing_minutes": 60,
        }
    ]


def test_a_doubled_hour_is_named_as_an_overlap() -> None:
    """Keying by local hour once gave both 02:00 rows the same UTC stamp."""
    day = _local_day(datetime(2026, 10, 25))
    doubled = dict(day[3])
    report = health.series_continuity(day + [doubled])
    assert report["overlaps"] == 1
    assert report["overlap_details"][0]["slot"] == doubled["start"]
    assert report["overlap_details"][0]["overlap_minutes"] == 60


def test_equivalent_spellings_of_one_slot_count_as_one_time() -> None:
    report = health.series_continuity(
        [
            {"start": "2026-10-25T02:00:00+01:00", "end": "2026-10-25T03:00:00+01:00"},
            {"start": "2026-10-25T02:00:00Z", "end": "2026-10-25T03:00:00Z"},
        ]
    )
    assert report["gaps"] == 0
    assert report["overlaps"] == 0


def test_bad_entries_are_counted_not_trusted() -> None:
    report = health.series_continuity(
        [
            {"start": "2026-10-25T00:00:00Z", "end": "2026-10-25T01:00:00Z"},
            {"start": "not a time", "end": "2026-10-25T02:00:00Z"},
            {"start": "2026-10-25T03:00:00", "end": "2026-10-25T04:00:00"},
            {"start": "2026-10-25T05:00:00Z", "end": "2026-10-25T05:00:00Z"},
            "nonsense",
        ]
    )
    assert report["slots"] == 1
    assert report["malformed"] == 4
    assert health.series_continuity(None) == {
        "slots": 0,
        "malformed": 0,
        "gaps": 0,
        "overlaps": 0,
    }


def test_only_the_first_few_gaps_are_listed() -> None:
    sparse = _local_day(datetime(2026, 10, 20))[::2]
    report = health.series_continuity(sparse)
    assert report["gaps"] == 11
    assert len(report["gap_details"]) == health.MAX_LISTED


def test_error_text_loses_the_query_and_stays_short() -> None:
    text = health.short_error(
        "429, message='Too Many Requests', "
        "url='https://api.example/v1/prices?country=de&plz=10115&hours=48'"
    )
    assert "10115" not in text
    assert "url='https://api.example/v1/prices?…'" in text
    assert len(health.short_error("x" * 500)) == health.MAX_ERROR_LENGTH
    assert health.short_error(TimeoutError()) == "TimeoutError"


def test_a_source_is_stale_after_a_failure_and_fresh_again_after_success() -> None:
    tracker = health.SourceHealth()
    first = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
    later = first + timedelta(minutes=30)

    assert tracker.status("price_series") is None
    tracker.failure("price_series", first, "offline")
    assert tracker.status("price_series") == "missing"

    tracker.success("price_series", first)
    assert tracker.status("price_series") == "fresh"

    tracker.failure("price_series", later, ConnectionError("offline"))
    assert tracker.status("price_series") == "stale"
    assert tracker.last_success("price_series") == first

    snapshot = tracker.snapshot(later + timedelta(minutes=5))
    assert snapshot["price_series"] == {
        "status": "stale",
        "last_success": "2026-10-28T10:00:00Z",
        "age_minutes": 35,
        "last_failure": "2026-10-28T10:30:00Z",
        "last_error": "offline",
    }

    tracker.success("price_series", later + timedelta(minutes=15))
    assert tracker.status("price_series") == "fresh"
