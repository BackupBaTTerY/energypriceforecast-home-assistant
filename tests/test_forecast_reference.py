"""A saved comparison must never be silently replaced or reconstructed."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.energypriceforecast.forecast_reference import (
    reference_entries,
    update_reference,
)

NOW = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)


def slot(value=0.1, source="forecast", start="2026-08-13T10:00:00Z",
         end="2026-08-13T10:15:00Z"):
    return {"start": start, "end": end, "value": value, "source": source}


def update(previous=None, entries=None, **kwargs):
    fresh = {"country": "DE", "unit": "EUR/kWh", "entries": entries or []}
    fresh.update(kwargs.pop("payload", {}))
    return update_reference(
        previous, fresh, now=kwargs.pop("now", NOW),
        zone=ZoneInfo("Europe/Berlin"), basis=kwargs.pop("basis", "base"),
        resolution=kwargs.pop("resolution", "15m"), **kwargs,
    )


def test_first_forecast_survives_revisions_and_official_publication():
    original = update(entries=[slot(0.10)])
    updated = update(original, [slot(0.20)])
    published = update(updated, [slot(0.15, "day_ahead")])
    assert original == updated == published
    assert reference_entries(published) == [{
        "start": "2026-08-13T10:00:00Z", "end": "2026-08-13T10:15:00Z",
        "value": 0.10, "captured_at": NOW.isoformat(),
    }]


def test_official_price_then_fallback_cannot_create_a_reference():
    known = update(entries=[slot(0.15, "day_ahead")])
    assert reference_entries(update(known, [slot(0.10)])) == []


def test_equivalent_timezones_identify_the_same_slot():
    original = update(entries=[slot()])
    changed = update(original, [slot(0.20, start="2026-08-13T12:00:00+02:00",
                                     end="2026-08-13T12:15:00+02:00")])
    assert changed == original


@pytest.mark.parametrize("value", [0, -0.02])
def test_zero_and_negative_are_real_forecasts(value):
    assert reference_entries(update(entries=[slot(value)]))[0]["value"] == value


@pytest.mark.parametrize("value", [None, True, "0.1", float("nan"), float("inf")])
@pytest.mark.parametrize("source", ["forecast", "day_ahead"])
def test_invalid_values_are_not_stored(value, source):
    cache = update(entries=[slot(value, source)])
    assert cache["slots"] == {}
    assert reference_entries(cache) == []


@pytest.mark.parametrize("start,end", [
    ("invalid", "2026-08-13T10:15:00Z"),
    ("2026-08-13T10:00:00", "2026-08-13T10:15:00Z"),
    ("2026-08-13T10:00:00Z", "2026-08-13T10:00:00Z"),
    ("2026-08-12T10:00:00Z", "2026-08-12T10:15:00Z"),
    ("2026-08-12T09:45:00Z", "2026-08-12T10:00:00Z"),
    ("2026-09-13T10:00:00Z", "2026-09-13T10:15:00Z"),
])
def test_invalid_past_current_and_unbounded_slots_are_not_captured(start, end):
    assert reference_entries(update(entries=[slot(start=start, end=end)])) == []


def test_midnight_keeps_the_incoming_days_previously_saved_forecast():
    cache = update(entries=[slot(), slot(start="2026-08-12T20:00:00Z",
                                        end="2026-08-12T20:15:00Z")])
    after = update(cache, now=datetime(2026, 8, 12, 22, 1, tzinfo=timezone.utc))
    assert len(reference_entries(after)) == 1
    assert reference_entries(after)[0]["start"] == "2026-08-13T10:00:00Z"


@pytest.mark.parametrize("kwargs", [
    {"payload": {"country": "NO4"}},
    {"payload": {"unit": "NOK/kWh"}},
    {"resolution": "1h"},
    {"basis": "formula|1.19|0.1"},
])
def test_changed_price_basis_starts_a_clean_comparison(kwargs):
    cache = update(entries=[slot()])
    changed = update(cache, [slot(0.20, "day_ahead")], **kwargs)
    assert reference_entries(changed) == []


def test_partial_response_does_not_erase_a_saved_forecast():
    cache = update(entries=[slot()])
    assert update(cache, payload={"entries": None}) == cache
