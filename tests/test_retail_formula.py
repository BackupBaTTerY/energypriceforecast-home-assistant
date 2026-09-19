"""Tests for the user's own retail formula: day-ahead x factor + surcharge."""
from __future__ import annotations

import copy

import pytest

from custom_components.energypriceforecast.retail_formula import (
    apply_to_series,
    apply_to_summary,
    plan_basis,
    retail_value,
)

# The example from the request that led to the formula: Dutch VAT as the
# factor, energy tax plus a supplier fee (both incl. VAT) as the surcharge.
NL_FACTOR = 1.21
NL_SURCHARGE = 0.1350


def _series(values: list[float | None]) -> dict:
    return {
        "format": "home-assistant-prices",
        "country": "NL",
        "unit": "EUR/kWh",
        "entries": [
            {
                "start": f"2026-09-19T{hour:02d}:00:00Z",
                "end": f"2026-09-19T{hour + 1:02d}:00:00Z",
                "value": value,
                "source": "day_ahead",
            }
            for hour, value in enumerate(values)
        ],
    }


def test_retail_value_applies_the_factor_before_the_surcharge() -> None:
    # 0.10 x 1.21 + 0.1350, not (0.10 + 0.1350) x 1.21.
    assert retail_value(0.10, NL_FACTOR, NL_SURCHARGE) == pytest.approx(0.256)


def test_retail_value_is_rounded_like_the_api() -> None:
    """Six decimals, so a state never reads 0.30000000000000004."""
    assert retail_value(0.1, 1.0, 0.2) == 0.3


def test_a_negative_day_ahead_price_stays_below_the_surcharge() -> None:
    """Negative hours are routine; the formula must not clip them."""
    assert retail_value(-0.02, NL_FACTOR, NL_SURCHARGE) == pytest.approx(0.1108)


def test_the_tariff_hook_sits_inside_the_factor() -> None:
    """A time-of-day grid charge excludes VAT like the day-ahead price."""
    assert retail_value(0.50, 1.25, 0.10, tariff=0.1674) == pytest.approx(
        1.25 * (0.50 + 0.1674) + 0.10
    )


def test_factor_one_and_no_surcharge_leave_the_price_alone() -> None:
    series = _series([0.10, -0.02, 0.30])

    assert apply_to_series(series, 1.0, 0.0) == series


def test_the_series_keeps_its_shape_and_its_order() -> None:
    """Same slots, same fields, same ranking - only the amounts change."""
    spot = [0.30, 0.10, -0.02, 0.25, 0.10]
    series = _series(spot)
    untouched = copy.deepcopy(series)

    retail = apply_to_series(series, NL_FACTOR, NL_SURCHARGE)

    assert series == untouched, "the base series must not be modified"
    assert retail["unit"] == "EUR/kWh"
    assert retail["country"] == "NL"
    values = [entry["value"] for entry in retail["entries"]]
    assert values == [retail_value(v, NL_FACTOR, NL_SURCHARGE) for v in spot]
    for base, priced in zip(series["entries"], retail["entries"]):
        assert {k: v for k, v in priced.items() if k != "value"} == {
            k: v for k, v in base.items() if k != "value"
        }
    # A positive factor and a constant surcharge cannot reorder the hours, so
    # every plan picks the same hours as on the day-ahead price.
    order = sorted(range(len(spot)), key=lambda i: (spot[i], i))
    assert sorted(range(len(values)), key=lambda i: (values[i], i)) == order


def test_a_slot_without_a_price_is_passed_through() -> None:
    retail = apply_to_series(_series([0.10, None]), NL_FACTOR, NL_SURCHARGE)

    assert retail["entries"][1]["value"] is None


@pytest.mark.parametrize("series", [None, {}])
def test_no_series_means_no_retail_series(series) -> None:
    assert apply_to_series(series, NL_FACTOR, NL_SURCHARGE) is None


def test_the_summary_converts_amounts_and_keeps_the_hours() -> None:
    summary = {
        "country": "NL",
        "flat": {
            "current_price": 0.20,
            "current_price_unit": "EUR/kWh",
            "cheapest_window_avg_price": 0.05,
            "best_price_window_avg_price": 0.05,
            "best_price_window_start": "2026-09-19T11:00:00Z",
            "best_price_window_end": "2026-09-19T15:00:00Z",
            "is_cheapest_window_now": True,
            "current_co2_g_kwh": 210.0,
            "best_co2_window_avg_g_co2_kwh": 150.0,
        },
    }
    untouched = copy.deepcopy(summary)

    retail = apply_to_summary(summary, NL_FACTOR, NL_SURCHARGE)

    assert summary == untouched, "the base summary must not be modified"
    flat = retail["flat"]
    assert flat["current_price"] == pytest.approx(0.377)
    assert flat["cheapest_window_avg_price"] == pytest.approx(0.1955)
    assert flat["best_price_window_avg_price"] == pytest.approx(0.1955)
    # Hours, flags and CO2 are not prices and stay exactly as they were.
    for key in (
        "current_price_unit",
        "best_price_window_start",
        "best_price_window_end",
        "is_cheapest_window_now",
        "current_co2_g_kwh",
        "best_co2_window_avg_g_co2_kwh",
    ):
        assert flat[key] == summary["flat"][key]
    assert retail["country"] == "NL"


def test_a_missing_price_in_the_summary_stays_missing() -> None:
    retail = apply_to_summary(
        {"flat": {"current_price": None}}, NL_FACTOR, NL_SURCHARGE
    )

    assert retail["flat"]["current_price"] is None


def test_the_plan_basis_names_the_formula() -> None:
    """A stored plan must not outlive the formula it was priced in."""
    assert plan_basis(NL_FACTOR, NL_SURCHARGE) == "formula|1.21|0.135"
    assert plan_basis(NL_FACTOR, NL_SURCHARGE) != plan_basis(NL_FACTOR, 0.14)
    assert plan_basis(1.0, 0.0) == "formula|1|0"
