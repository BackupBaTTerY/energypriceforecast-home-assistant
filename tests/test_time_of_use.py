"""Tests for grid charges that change with the time of day."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.energypriceforecast.time_of_use import (
    HourlyTable,
    Rates,
    Schedule,
    in_month_range,
    in_window,
    mean_rate,
    schedule_from_config,
    WEEKEND_LIKE_WEEKDAY,
    WEEKEND_LOW,
)

DK = ZoneInfo("Europe/Copenhagen")
UTC = timezone.utc

# TREFOR El-net's household tariff "Nettarif C", DKK/kWh without VAT: night
# 00-06, day, and the evening peak 17-21, in the two seasons that differ most.
SUMMER = Rates(low=0.0429, standard=0.0644, peak=0.1674)
WINTER = Rates(low=0.0653, standard=0.1959, peak=0.5877)


def _danish(weekend: str = WEEKEND_LIKE_WEEKDAY, seasonal: bool = True) -> Schedule:
    return Schedule(
        low_start=0,
        low_end=6,
        peak_start=17,
        peak_end=21,
        weekend=weekend,
        summer=SUMMER,
        zone=DK,
        winter=WINTER if seasonal else None,
        winter_from=1 if seasonal else 0,
        winter_to=3 if seasonal else 0,
    )


@pytest.mark.parametrize(
    ("hour", "start", "end", "expected"),
    [
        (0, 0, 6, True),
        (5, 0, 6, True),
        (6, 0, 6, False),
        (23, 22, 6, True),
        (5, 22, 6, True),
        (6, 22, 6, False),
        (21, 22, 6, False),
        # An unset window must match nothing, not everything.
        (12, 0, 0, False),
        (0, 0, 0, False),
    ],
)
def test_in_window(hour, start, end, expected) -> None:
    assert in_window(hour, start, end) is expected


@pytest.mark.parametrize(
    ("month", "first", "last", "expected"),
    [
        (1, 11, 3, True),
        (12, 11, 3, True),
        (3, 11, 3, True),
        (4, 11, 3, False),
        (10, 11, 3, False),
        (2, 1, 3, True),
        (6, 1, 3, False),
        # No season configured.
        (1, 0, 0, False),
    ],
)
def test_in_month_range(month, first, last, expected) -> None:
    assert in_month_range(month, first, last) is expected


@pytest.mark.parametrize(
    ("local_hour", "level_rate"),
    [
        (3, SUMMER.low),
        (6, SUMMER.standard),
        (12, SUMMER.standard),
        (17, SUMMER.peak),
        (20, SUMMER.peak),
        (21, SUMMER.standard),
    ],
)
def test_the_three_levels_of_a_summer_day(local_hour, level_rate) -> None:
    moment = datetime(2026, 7, 15, local_hour, tzinfo=DK)

    assert _danish().rate_at(moment) == pytest.approx(level_rate)


def test_winter_months_use_their_own_amounts() -> None:
    """Same hour, same window - four times the price in January."""
    schedule = _danish()

    assert schedule.rate_at(datetime(2026, 7, 15, 18, tzinfo=DK)) == pytest.approx(
        SUMMER.peak
    )
    assert schedule.rate_at(datetime(2026, 1, 15, 18, tzinfo=DK)) == pytest.approx(
        WINTER.peak
    )


def test_a_schedule_without_a_season_keeps_one_set_of_amounts() -> None:
    schedule = _danish(seasonal=False)

    assert schedule.rate_at(datetime(2026, 1, 15, 18, tzinfo=DK)) == pytest.approx(
        SUMMER.peak
    )


def test_the_weekend_rule_can_price_saturday_and_sunday_low() -> None:
    """Flanders, Finland, Norway and Poland's G12w do it this way."""
    saturday = datetime(2026, 7, 18, 18, tzinfo=DK)
    friday = datetime(2026, 7, 17, 18, tzinfo=DK)

    assert _danish(weekend=WEEKEND_LOW).rate_at(saturday) == pytest.approx(SUMMER.low)
    assert _danish(weekend=WEEKEND_LOW).rate_at(friday) == pytest.approx(SUMMER.peak)
    # Denmark itself does not: there the weekend is an ordinary day.
    assert _danish().rate_at(saturday) == pytest.approx(SUMMER.peak)


def test_the_clock_is_the_markets_own_one() -> None:
    """Prices arrive in UTC, but a tariff window is local time.

    In summer Denmark is two hours ahead of UTC, so 16:00 UTC is 18:00 in
    Copenhagen and already the evening peak. Judging it in UTC would put the
    peak two hours late and move every plan with it.
    """
    schedule = _danish()

    assert schedule.rate_at(datetime(2026, 7, 15, 16, tzinfo=UTC)) == pytest.approx(
        SUMMER.peak
    )
    assert schedule.rate_at(datetime(2026, 7, 15, 14, tzinfo=UTC)) == pytest.approx(
        SUMMER.standard
    )
    # In winter the offset is one hour, and the same UTC hour is not peak.
    assert schedule.rate_at(datetime(2026, 1, 15, 16, tzinfo=UTC)) == pytest.approx(
        WINTER.peak
    )
    # 23:00 UTC is midnight in Copenhagen, where the night rate starts.
    assert schedule.rate_at(datetime(2026, 1, 15, 23, tzinfo=UTC)) == pytest.approx(
        WINTER.low
    )
    assert schedule.rate_at(datetime(2026, 1, 15, 22, tzinfo=UTC)) == pytest.approx(
        WINTER.standard
    )


def test_a_two_level_tariff_has_no_peak_window() -> None:
    """Norway, Finland and Belgium: one cheap window, one price for the rest."""
    schedule = Schedule(
        low_start=22,
        low_end=6,
        peak_start=0,
        peak_end=0,
        weekend=WEEKEND_LOW,
        summer=Rates(low=0.1856, standard=0.2456, peak=0.0),
        zone=ZoneInfo("Europe/Oslo"),
    )

    assert schedule.rate_at(datetime(2026, 7, 15, 23, tzinfo=DK)) == pytest.approx(
        0.1856
    )
    assert schedule.rate_at(datetime(2026, 7, 15, 12, tzinfo=DK)) == pytest.approx(
        0.2456
    )


def test_the_key_changes_with_every_amount_and_window() -> None:
    """A stored plan must not outlive the tariff it was priced with."""
    base = _danish()

    assert base.key() == _danish().key()
    assert base.key() != _danish(weekend=WEEKEND_LOW).key()
    assert base.key() != _danish(seasonal=False).key()
    cheaper = Schedule(
        low_start=0,
        low_end=6,
        peak_start=17,
        peak_end=21,
        weekend=WEEKEND_LIKE_WEEKDAY,
        summer=Rates(low=0.0429, standard=0.0644, peak=0.1),
        zone=DK,
        winter=WINTER,
        winter_from=1,
        winter_to=3,
    )
    assert base.key() != cheaper.key()


def _table(**hours: float) -> HourlyTable:
    prices = tuple(hours.get(f"h{hour}", 0.05) for hour in range(24))
    return HourlyTable(prices={date(2026, 9, 21): prices}, zone=DK, name="radius|C")


def test_an_hourly_table_prices_each_hour_from_its_day() -> None:
    table = _table(h3=0.01, h18=0.50)

    assert table.rate_at(datetime(2026, 9, 21, 3, tzinfo=DK)) == pytest.approx(0.01)
    assert table.rate_at(datetime(2026, 9, 21, 18, tzinfo=DK)) == pytest.approx(0.50)
    assert table.rate_at(datetime(2026, 9, 21, 12, tzinfo=DK)) == pytest.approx(0.05)


def test_a_day_the_table_does_not_cover_has_no_rate() -> None:
    """Better a visible gap than an hour priced with last week's tariff."""
    assert _table().rate_at(datetime(2026, 9, 22, 12, tzinfo=DK)) is None


def test_mean_rate_averages_a_window_that_spans_levels() -> None:
    """16:00-20:00 in Copenhagen: one standard hour, three peak hours."""
    schedule = _danish()

    average = mean_rate(
        schedule,
        datetime(2026, 7, 15, 16, tzinfo=DK),
        datetime(2026, 7, 15, 20, tzinfo=DK),
    )

    assert average == pytest.approx((SUMMER.standard + 3 * SUMMER.peak) / 4)


def test_mean_rate_without_a_rate_for_every_hour() -> None:
    table = _table()

    assert (
        mean_rate(
            table,
            datetime(2026, 9, 21, 23, tzinfo=DK),
            datetime(2026, 9, 22, 2, tzinfo=DK),
        )
        is None
    )


def test_mean_rate_of_an_empty_window() -> None:
    moment = datetime(2026, 7, 15, 16, tzinfo=DK)

    assert mean_rate(_danish(), moment, moment) is None


def test_schedule_from_config_reads_the_stored_keys() -> None:
    schedule = schedule_from_config(
        {
            "tou_low_start": 0,
            "tou_low_end": 6,
            "tou_peak_start": 17,
            "tou_peak_end": 21,
            "tou_weekend": WEEKEND_LOW,
            "tou_rate_low": 0.0429,
            "tou_rate_standard": 0.0644,
            "tou_rate_peak": 0.1674,
            "tou_winter": True,
            "tou_winter_from": 10,
            "tou_winter_to": 3,
            "tou_winter_rate_low": 0.0653,
            "tou_winter_rate_standard": 0.1959,
            "tou_winter_rate_peak": 0.5877,
        },
        DK,
    )

    assert schedule.low_end == 6
    assert schedule.weekend == WEEKEND_LOW
    assert schedule.summer.peak == pytest.approx(0.1674)
    assert schedule.winter is not None
    assert schedule.winter.peak == pytest.approx(0.5877)
    assert schedule.rate_at(datetime(2026, 11, 3, 18, tzinfo=DK)) == pytest.approx(
        0.5877
    )


def test_a_config_without_winter_prices_has_one_season() -> None:
    schedule = schedule_from_config(
        {"tou_rate_low": 0.04, "tou_rate_standard": 0.06, "tou_low_start": 0,
         "tou_low_end": 6},
        DK,
    )

    assert schedule.winter is None
    assert schedule.rate_at(datetime(2026, 1, 15, 3, tzinfo=DK)) == pytest.approx(0.04)
