"""The own price source (1.21.0): formats, units, mixing and the save check.

Plain Python, no Home Assistant instance: own_prices.py is kept free of it so
that the rules which decide what a household pays by can be read and tested
on their own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.energypriceforecast.own_prices import (
    FORMAT_ENTSOE,
    FORMAT_NORDPOOL,
    OWN_SOURCE,
    OwnSlot,
    convert,
    level_check,
    merge,
    parse_unit,
    series_currency,
    signature,
    slots_from_attributes,
    slots_from_service_response,
)

BERLIN = ZoneInfo("Europe/Berlin")
HELSINKI = ZoneInfo("Europe/Helsinki")
UTC = timezone.utc


def _hourly(start: datetime, values: list[float]) -> list[OwnSlot]:
    return [
        OwnSlot(start + timedelta(hours=i), start + timedelta(hours=i + 1), v)
        for i, v in enumerate(values)
    ]


def _series(
    start: datetime, values: list[float], source: str, minutes: int = 15, **extra
) -> dict:
    step = timedelta(minutes=minutes)
    return {
        "unit": "EUR/kWh",
        "entries": [
            {
                "start": (start + step * i).isoformat(),
                "end": (start + step * (i + 1)).isoformat(),
                "value": value,
                "source": source,
                **extra,
            }
            for i, value in enumerate(values)
        ],
    }


# Units --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "series", "expected"),
    [
        ("EUR/kWh", None, ("EUR", 1.0)),
        ("€/kWh", None, ("EUR", 1.0)),
        ("c/kWh", None, ("EUR", 0.01)),
        ("ct/kWh", None, ("EUR", 0.01)),
        ("EUR/MWh", None, ("EUR", 0.001)),
        ("€/MWh", None, ("EUR", 0.001)),
        ("SEK/kWh", None, ("SEK", 1.0)),
        ("Öre/kWh", None, ("SEK", 0.01)),
        ("NOK/MWh", None, ("NOK", 0.001)),
        ("Øre/kWh", "NOK", ("NOK", 0.01)),
        ("Øre/kWh", "DKK", ("DKK", 0.01)),
        ("EUR/Wh", None, ("EUR", 1000.0)),
    ],
)
def test_units_are_read_with_their_factor(unit, series, expected) -> None:
    currency, factor = parse_unit(unit, series)
    assert currency == expected[0]
    assert factor == pytest.approx(expected[1])


@pytest.mark.parametrize(
    ("unit", "series"),
    [
        (None, None),
        ("kWh", None),
        ("EUR", None),
        ("Gold/kWh", None),
        ("EUR/GJ", None),
        # Danish or Norwegian? Only the series can say, and here it cannot.
        ("Øre/kWh", "EUR"),
        ("Øre/kWh", None),
    ],
)
def test_a_unit_that_is_not_understood_is_never_guessed(unit, series) -> None:
    assert parse_unit(unit, series) is None


def test_the_series_currency_comes_from_its_unit() -> None:
    assert series_currency("EUR/kWh") == "EUR"
    assert series_currency("NOK/kWh") == "NOK"
    assert series_currency(None) is None


# Formats ------------------------------------------------------------------


def test_nordpool_attributes_with_datetimes_are_read() -> None:
    """Nord Pool from HACS keeps datetime objects, not text, in its state."""
    start = datetime(2026, 11, 20, 0, 0, tzinfo=HELSINKI)
    attributes = {
        "unit_of_measurement": "c/kWh",
        "raw_today": [
            {"start": start, "end": start + timedelta(hours=1), "value": 5.0},
            {"start": start + timedelta(hours=1), "end": start + timedelta(hours=2), "value": None},
        ],
        "raw_tomorrow": [
            {
                "start": (start + timedelta(days=1)).isoformat(),
                "end": (start + timedelta(days=1, hours=1)).isoformat(),
                "value": "7.5",
            }
        ],
    }
    found, raw = slots_from_attributes(attributes)
    assert found == FORMAT_NORDPOOL
    # The junk value is dropped, text values are read.
    assert [(slot[0], slot[2]) for slot in raw] == [
        (start, 5.0),
        (start + timedelta(days=1), 7.5),
    ]


def test_entsoe_prices_get_their_end_from_the_step() -> None:
    """ENTSO-e names only the start, as str(datetime), every quarter hour."""
    start = datetime(2026, 11, 20, 0, 0, tzinfo=BERLIN)
    prices = [
        {"time": str(start + timedelta(minutes=15 * i)), "price": 0.10 + i / 100}
        for i in range(4)
    ]
    found, raw = slots_from_attributes(
        {"prices_today": prices, "prices_tomorrow": [], "prices": prices}
    )
    assert found == FORMAT_ENTSOE
    assert len(raw) == 4
    assert all(end - begin == timedelta(minutes=15) for begin, end, _ in raw)
    assert raw[-1][2] == pytest.approx(0.13)


def test_an_entity_without_a_price_list_has_no_format() -> None:
    assert slots_from_attributes({"unit_of_measurement": "EUR/kWh"}) == (None, [])
    assert slots_from_attributes(None) == (None, [])


def test_the_core_service_response_is_read_per_area() -> None:
    response = {
        "FI": [
            {
                "start": "2026-11-19T23:00:00+00:00",
                "end": "2026-11-19T23:15:00+00:00",
                "price": 52.3,
            }
        ]
    }
    raw = slots_from_service_response(response, "FI")
    assert raw[0][2] == 52.3
    assert slots_from_service_response(response, "SE3") == []


def test_convert_scales_sorts_and_drops_duplicates() -> None:
    start = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)
    raw = [
        (start + timedelta(hours=1), start + timedelta(hours=2), 60.0),
        (start, start + timedelta(hours=1), 50.0),
        (start, start + timedelta(hours=1), 55.0),
    ]
    slots = convert(raw, 0.001)
    assert [slot.value for slot in slots] == pytest.approx([0.055, 0.060])
    assert signature(slots) != signature(slots[:1])
    assert signature([]) == ()


# Mixing -------------------------------------------------------------------


def test_forecast_quarters_take_the_price_of_their_hour() -> None:
    start = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    series = _series(start, [0.30] * 8, "forecast", spike_possible=True)
    mixed, report = merge(
        series,
        _hourly(start, [0.05]),
        now=start - timedelta(hours=12),
        zone=BERLIN,
        step_minutes=15,
        currency="EUR",
    )
    entries = mixed["entries"]
    assert [entry["value"] for entry in entries] == [0.05] * 4 + [0.30] * 4
    assert [entry["source"] for entry in entries] == [OWN_SOURCE] * 4 + ["forecast"] * 4
    # A known price is no longer a forecast that might spike.
    assert "spike_possible" not in entries[0]
    assert entries[4]["spike_possible"] is True
    assert report.replaced == 4
    # The series given in is not changed.
    assert series["entries"][0]["value"] == 0.30


def test_an_hourly_series_takes_the_mean_of_four_quarter_prices() -> None:
    start = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    quarters = [
        OwnSlot(start + timedelta(minutes=15 * i), start + timedelta(minutes=15 * (i + 1)), v)
        for i, v in enumerate([0.04, 0.08, 0.12, 0.16])
    ]
    mixed, _ = merge(
        _series(start, [0.30], "forecast", minutes=60),
        quarters,
        now=start - timedelta(hours=12),
        zone=BERLIN,
        step_minutes=60,
    )
    assert mixed["entries"][0]["value"] == pytest.approx(0.10)


def test_a_slot_only_partly_covered_keeps_its_forecast() -> None:
    start = datetime(2026, 11, 21, 0, 0, tzinfo=UTC)
    half = [OwnSlot(start, start + timedelta(minutes=30), 0.05)]
    mixed, report = merge(
        _series(start, [0.30], "forecast", minutes=60),
        half,
        now=start - timedelta(hours=12),
        zone=BERLIN,
        step_minutes=60,
    )
    assert mixed["entries"][0]["value"] == 0.30
    assert mixed["entries"][0]["source"] == "forecast"
    assert report.replaced == 0


def test_published_prices_of_the_api_stay_and_a_disagreement_is_reported() -> None:
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    series = _series(start, [0.10, 0.10, 0.10, 0.10], "day_ahead")
    own = [
        OwnSlot(start, start + timedelta(minutes=30), 0.102),  # rounding: fine
        OwnSlot(start + timedelta(minutes=30), start + timedelta(hours=1), 0.125),
    ]
    mixed, report = merge(
        series, own, now=start, zone=BERLIN, step_minutes=15, currency="EUR"
    )
    assert [entry["value"] for entry in mixed["entries"]] == [0.10] * 4
    assert [entry["source"] for entry in mixed["entries"]] == ["day_ahead"] * 4
    assert report.compared == 4
    assert report.disagreement_count == 2
    assert report.disagreements[0]["own_source"] == pytest.approx(0.125)


def test_the_hours_of_today_already_past_are_filled_where_the_api_has_none() -> None:
    """A forecast-only zone: the API's series starts now, the source at 00:00."""
    now = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)  # 03:00 in Helsinki
    midnight = datetime(2026, 11, 19, 22, 0, tzinfo=UTC)  # 00:00 in Helsinki
    series = _series(now, [0.30] * 4, "forecast", minutes=60)
    mixed, report = merge(
        series,
        _hourly(midnight, [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]),
        now=now,
        zone=HELSINKI,
        step_minutes=60,
    )
    entries = mixed["entries"]
    assert report.added == 3
    assert report.replaced == 4
    assert [entry["value"] for entry in entries] == pytest.approx(
        [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
    )
    assert all(entry["source"] == OWN_SOURCE for entry in entries)
    starts = [datetime.fromisoformat(entry["start"]) for entry in entries]
    assert starts == sorted(starts)


def test_nothing_is_filled_beyond_tomorrow() -> None:
    now = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    far = datetime(2026, 11, 22, 0, 0, tzinfo=UTC)  # the day after tomorrow
    mixed, report = merge(
        {"unit": "EUR/kWh", "entries": []},
        _hourly(far, [0.05]),
        now=now,
        zone=UTC,
        step_minutes=60,
    )
    assert mixed["entries"] == []
    assert report.added == 0


def test_without_slots_the_series_is_returned_unchanged() -> None:
    series = {"unit": "EUR/kWh", "entries": []}
    mixed, report = merge(
        series, [], now=datetime.now(UTC), zone=UTC, step_minutes=15
    )
    assert mixed is series
    assert report.as_dict()["replaced_forecast_slots"] == 0


# The save check -----------------------------------------------------------


def test_the_same_auction_prices_pass_the_check() -> None:
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    values = [0.08, 0.10, 0.12, 0.09]
    series = _series(start, values, "day_ahead", minutes=60)
    assert level_check(_hourly(start, values), series["entries"], "EUR") is None


def test_vat_in_the_source_shows_against_published_prices() -> None:
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    values = [0.08, 0.10, 0.12, 0.09]
    series = _series(start, values, "day_ahead", minutes=60)
    with_vat = _hourly(start, [value * 1.255 for value in values])
    assert level_check(with_vat, series["entries"], "EUR") == "published_mismatch"


def test_charges_in_the_source_show_against_the_forecast() -> None:
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    forecast = [0.08, 0.10, 0.12, 0.09, 0.07, 0.11]
    series = _series(start, forecast, "forecast", minutes=60)
    # Nord Pool with VAT and a grid charge of 5 ct in its template.
    gross = _hourly(start, [value * 1.255 + 0.05 for value in forecast])
    assert level_check(gross, series["entries"], "EUR") == "forecast_mismatch"


def test_an_ordinary_forecast_miss_is_not_held_against_the_source() -> None:
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    forecast = [0.08, 0.10, 0.12, 0.09, 0.07, 0.11]
    series = _series(start, forecast, "forecast", minutes=60)
    actual = _hourly(start, [0.095, 0.085, 0.14, 0.10, 0.06, 0.125])
    assert level_check(actual, series["entries"], "EUR") is None


def test_a_wrong_factor_of_a_thousand_shows_against_the_forecast() -> None:
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    forecast = [0.08, 0.10, 0.12, 0.09]
    series = _series(start, forecast, "forecast", minutes=60)
    per_mwh = _hourly(start, [value * 1000 for value in forecast])
    assert level_check(per_mwh, series["entries"], "EUR") == "forecast_mismatch"


def test_too_little_overlap_judges_nothing() -> None:
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    series = _series(start, [0.10, 0.10], "day_ahead", minutes=60)
    assert level_check(_hourly(start, [0.50, 0.50]), series["entries"], "EUR") is None
