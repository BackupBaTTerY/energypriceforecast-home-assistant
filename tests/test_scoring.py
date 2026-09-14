"""Tests for the combined score for now.

Synthetic series, so each property the design rests on shows on its own: the
scale is used end to end, forecasts stay out of the reference, a quantity that
barely moves barely counts, one spike does not set the scale, and CO2 changes
the verdict when it does move.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.energypriceforecast.scoring import combined_score_now

START = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


def _slots(
    values: list[float], source: str | None = "day_ahead", minutes: int = 60
) -> list[dict]:
    return [
        {
            "start": (START + timedelta(minutes=minutes * i)).isoformat(),
            "end": (START + timedelta(minutes=minutes * (i + 1))).isoformat(),
            "value": value,
            "source": source,
        }
        for i, value in enumerate(values)
    ]


def _at(minutes: int) -> datetime:
    return START + timedelta(minutes=minutes)


def test_the_best_slot_ahead_scores_100_and_the_worst_0() -> None:
    best = combined_score_now(_slots([0.10] + [0.30] * 23), None, _at(5))
    worst = combined_score_now(_slots([0.90] + [0.30] * 23), None, _at(5))

    assert best.score == 100.0
    assert worst.score == 0.0


def test_ties_count_half() -> None:
    result = combined_score_now(_slots([0.25] * 24), None, _at(5))

    assert result.score == 50.0


def test_forecast_prices_stay_out_of_the_reference() -> None:
    """Tomorrow's frozen forecast ran too low; ranking against it misleads."""
    entries = _slots([0.20] + [0.30] * 11) + [
        {**slot, "source": "forecast"} for slot in _slots([0.01] * 24)[12:]
    ]

    result = combined_score_now(entries, None, _at(5))

    # Against the forecast the present would rank near the bottom; against
    # the published hours it is the cheapest slot there is.
    assert result.score == 100.0
    assert result.reference_end == START + timedelta(hours=12)
    assert result.reference_slots == 12


def test_too_few_published_hours_ahead_give_no_score() -> None:
    assert combined_score_now(_slots([0.20, 0.30, 0.40]), None, _at(5)) is None


def test_no_published_price_for_the_present_gives_no_score() -> None:
    assert combined_score_now(_slots([0.2] * 24, source="forecast"), None, _at(5)) is None
    # An entry that does not say what it is counts as a forecast.
    assert combined_score_now(_slots([0.2] * 24, source=None), None, _at(5)) is None


def test_co2_that_barely_moves_does_not_reorder_the_hours() -> None:
    """A hydro grid at 20-21 g all day: a tenth of a gram must not swing it."""
    prices = [0.20 + 0.01 * ((7 * i) % 24) for i in range(24)]
    co2 = [20.0 if i % 2 else 20.3 for i in range(24)]

    for hour in (0, 3, 9, 14):
        moment = _at(60 * hour + 5)
        with_co2 = combined_score_now(_slots(prices), _slots(co2, source=None), moment)
        price_only = combined_score_now(_slots(prices), None, moment)
        assert with_co2.score == price_only.score


def test_one_price_spike_does_not_set_the_scale() -> None:
    prices = [0.20 + 0.01 * i for i in range(23)] + [5.00]

    result = combined_score_now(_slots(prices), None, _at(5))

    # The 10th-to-90th percentile range leaves the spike out; max minus min
    # would have been almost five euros and flattened every other difference.
    assert result.price_spread < 0.25


def test_co2_changes_the_verdict_when_it_varies() -> None:
    """A clean hour at an ordinary price outranks a cheap but dirty one."""
    prices = [0.20, 0.15] + [0.22] * 22
    co2 = [150.0, 470.0] + [430.0] * 22

    combined = combined_score_now(_slots(prices), _slots(co2, source=None), _at(5))
    price_only = combined_score_now(_slots(prices), None, _at(5))

    assert combined.score == 100.0
    assert price_only.score < 100.0


def test_without_co2_the_score_is_a_price_score_and_says_so() -> None:
    result = combined_score_now(_slots([0.10] + [0.30] * 23), None, _at(5))

    assert result.co2_part is None
    assert result.co2_share is None
    assert result.co2_spread is None


def test_co2_share_shows_what_drives_the_ranking() -> None:
    prices = [0.20 + 0.01 * ((7 * i) % 24) for i in range(24)]
    wide_co2 = [100.0 + 20.0 * ((5 * i) % 24) for i in range(24)]

    flat = combined_score_now(_slots(prices), _slots([20.0] * 24, source=None), _at(5))
    wide = combined_score_now(_slots([0.25] * 24), _slots(wide_co2, source=None), _at(5))

    assert flat.co2_share == pytest.approx(0.0)
    assert wide.co2_share == pytest.approx(1.0)


def test_quarter_hour_slots_are_ranked_at_their_own_resolution() -> None:
    result = combined_score_now(_slots([0.10] + [0.30] * 95, minutes=15), None, _at(5))

    assert result.reference_slots == 96
    assert result.score == 100.0
