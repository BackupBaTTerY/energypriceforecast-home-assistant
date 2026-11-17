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
    InvalidWindows,
    format_windows,
    in_window,
    in_windows,
    mean_rate,
    parse_windows,
    schedule_from_config,
    windows_from_hours,
    windows_overlap,
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
        low=windows_from_hours(0, 6),
        peak=windows_from_hours(17, 21),
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
        low=windows_from_hours(22, 6),
        peak=(),
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
        low=windows_from_hours(0, 6),
        peak=windows_from_hours(17, 21),
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

    assert schedule.low == ((0, 360),)
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


# Several windows per level, on the quarter hour (1.19.0): the shape of a
# German Modul 3 price sheet, where each operator sets its own windows.

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ()),
        (None, ()),
        ("22:00-06:00", ((1320, 360),)),
        ("22-6", ((1320, 360),)),
        ("06:00-08:00, 17:15-20:45", ((360, 480), (1035, 1245))),
        ("06:00 \u2013 08:00; 17:15\u201320:45\n", ((360, 480), (1035, 1245))),
        # Midnight as an end is a day's end, the same window as "18 to 0".
        ("18:00-24:00", ((1080, 0),)),
    ],
)
def test_windows_are_read_from_text(text, expected) -> None:
    assert parse_windows(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "17:10-20:00",  # off the quarter hour: a typo, not a tariff
        "17:00",
        "17:00-17:00",
        "00:00-24:00",
        "25:00-06:00",
        "06:60-08:00",
        "abends",
    ],
)
def test_text_that_is_not_a_window_is_refused(text) -> None:
    with pytest.raises(InvalidWindows):
        parse_windows(text)


def test_windows_are_written_back_in_one_spelling() -> None:
    assert format_windows(parse_windows("6-8,17:15 - 20:45")) == "06:00-08:00, 17:15-20:45"
    assert format_windows(parse_windows("18-24")) == "18:00-00:00"
    assert format_windows(()) == ""
    assert windows_from_hours(17, 17) == ()


def test_in_windows_covers_every_window_and_the_night() -> None:
    windows = parse_windows("06:00-08:00, 17:15-20:45, 23:00-01:00")
    inside = [360, 479, 1035, 1244, 1380, 0, 59]
    outside = [480, 1034, 1245, 60, 720]
    assert all(in_windows(m, windows) for m in inside)
    assert not any(in_windows(m, windows) for m in outside)


def test_overlap_is_judged_on_the_quarter_hour() -> None:
    assert windows_overlap(parse_windows("22:00-06:00"), parse_windows("05:45-08:00"))
    assert not windows_overlap(parse_windows("22:00-06:00"), parse_windows("06:00-08:00"))
    assert not windows_overlap((), parse_windows("06:00-08:00"))


def _modul_3() -> Schedule:
    """Two peak windows, one of them starting at a quarter past."""
    return Schedule(
        low=parse_windows("00:00-06:00, 13:00-15:00"),
        peak=parse_windows("07:00-09:00, 17:15-20:45"),
        weekend=WEEKEND_LIKE_WEEKDAY,
        summer=Rates(low=0.03, standard=0.08, peak=0.14),
        zone=ZoneInfo("Europe/Berlin"),
    )


@pytest.mark.parametrize(
    ("hour", "minute", "rate"),
    [
        (5, 45, 0.03),
        (7, 30, 0.14),
        (9, 0, 0.08),
        (14, 0, 0.03),
        (17, 0, 0.08),
        (17, 15, 0.14),
        (20, 30, 0.14),
        (20, 45, 0.08),
    ],
)
def test_a_modul_3_day_is_priced_quarter_by_quarter(hour, minute, rate) -> None:
    moment = datetime(2026, 11, 17, hour, minute, tzinfo=ZoneInfo("Europe/Berlin"))
    assert _modul_3().rate_at(moment) == pytest.approx(rate)


def test_an_hour_with_a_quarter_past_start_costs_the_average() -> None:
    """17:00-18:00: one standard quarter, three peak quarters."""
    berlin = ZoneInfo("Europe/Berlin")
    average = mean_rate(
        _modul_3(),
        datetime(2026, 11, 17, 17, tzinfo=berlin),
        datetime(2026, 11, 17, 18, tzinfo=berlin),
    )
    assert average == pytest.approx((0.08 + 3 * 0.14) / 4)


def test_tariffs_in_whole_hours_keep_their_key() -> None:
    """Updating must not discard the stored plans of an unchanged tariff."""
    assert _danish().key() == (
        "tou|0-6|17-21|like_weekday|0.0429/0.0644/0.1674|1-3:0.0653/0.1959/0.5877"
    )
    assert "17:15-20:45" in _modul_3().key()


def test_windows_stored_as_text_win_over_old_hours() -> None:
    data = {
        "tou_low_start": 0,
        "tou_low_end": 6,
        "tou_peak_start": 17,
        "tou_peak_end": 21,
        "tou_low_windows": "22:00-06:00",
        "tou_peak_windows": "",
        "tou_rate_low": 0.03,
        "tou_rate_standard": 0.08,
        "tou_rate_peak": 0.14,
    }
    schedule = schedule_from_config(data, DK)
    assert schedule.low == ((1320, 360),)
    # Cleared on purpose: no peak window, whatever the old hours said.
    assert schedule.peak == ()
    assert schedule.rate_at(datetime(2026, 7, 15, 18, tzinfo=DK)) == pytest.approx(0.08)


def test_unreadable_stored_text_means_no_window_rather_than_a_crash() -> None:
    schedule = schedule_from_config({"tou_peak_windows": "kaputt"}, DK)
    assert schedule.peak == ()
