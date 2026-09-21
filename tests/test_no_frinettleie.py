"""Tests for prefilling a Norwegian tariff from the fri-nettleie collection.

The snapshot below copies the collection's real format, including the three
shapes that occur: a weekday daytime exception, an exception without days,
and seasonal exceptions that only apply in some months.
"""
from __future__ import annotations

import pytest

from custom_components.energypriceforecast.no_frinettleie import (
    async_load_operators,
    parse_snapshot,
    prefill,
)
from custom_components.energypriceforecast.time_of_use import (
    WEEKEND_LIKE_WEEKDAY,
    WEEKEND_LOW,
)

SNAPSHOT = """# fri-nettleie - Nettleietariffer for Norge
> Generert: 2026-09-18T05:54:44 | Gyldige tariffer per: 2026-09-18

## Energiledd
`grunnpris` er standardprisen i øre/kWh som gjelder når ingen unntak treffer.

## Elvia AS
GLN: 7080005046220 | Sist oppdatert: 2026-06-05
Kundegrupper: husholdning, fritid, liten_næring
Gyldig fra: 2026-07-01
Fastledd metode: TRE_DØGNMAX_MND | Terskel inkludert: True
Fastledd terskler (kr/år):
  - Fra 0 kW: 1440 kr/år
Energiledd grunnpris: 16.99 øre/kWh (eks. avgifter)
Energiledd unntak:
  - Virkedag: 28.99 øre/kWh | timer 6-21 | dager: virkedag


---

## Tensio TS AS
GLN: 7080005051880 | Sist oppdatert: 2026-06-19
Kundegrupper: husholdning, fritid, liten_næring
Energiledd grunnpris: 10.902 øre/kWh (eks. avgifter)
Energiledd unntak:
  - Dag: 22.102 øre/kWh | timer 6-21

---

## Lede AS
GLN: 7080005051279 | Sist oppdatert: 2026-04-15
Kundegrupper: husholdning, fritid
Energiledd grunnpris: 11.41 øre/kWh (eks. avgifter)

---

## Sesongnett AS
GLN: 7080000000002 | Sist oppdatert: 2026-05-01
Kundegrupper: husholdning
Energiledd grunnpris: 26.89 øre/kWh (eks. avgifter)
Energiledd unntak:
  - Lavlast sommer: 24.89 øre/kWh | timer 22-5 | måneder: april, mai, juni
  - Høylast vinter: 29.89 øre/kWh | timer 6-21 | måneder: januar, februar, mars

---

## Industrinett AS
GLN: 7080000000003 | Sist oppdatert: 2026-01-01
Kundegrupper: liten_næring
Energiledd grunnpris: 20.00 øre/kWh (eks. avgifter)
"""


def _by_name(name):
    return next(o for o in parse_snapshot(SNAPSHOT) if o.name == name)


def test_only_household_operators_are_read() -> None:
    names = [operator.name for operator in parse_snapshot(SNAPSHOT)]

    # The format's own headings are not operators, and a business-only
    # tariff is not what a household pays.
    assert names == ["Elvia AS", "Tensio TS AS", "Lede AS", "Sesongnett AS"]


def test_prices_arrive_in_kroner() -> None:
    """The collection states oere; the price sensors show kroner."""
    elvia = _by_name("Elvia AS")

    assert elvia.base_rate == pytest.approx(0.1699)
    assert elvia.exceptions[0]["price"] == pytest.approx(0.2899)
    assert elvia.updated == "2026-06-05"


def test_a_weekday_daytime_exception_makes_nights_and_weekends_cheap() -> None:
    """Elvia: 28.99 oere on weekdays 06-22, the base price at all other times."""
    values = prefill(_by_name("Elvia AS"))

    assert values["tou_rate_low"] == pytest.approx(0.1699)
    assert values["tou_rate_standard"] == pytest.approx(0.2899)
    # "timer 6-21" means 06:00 up to 22:00, so the night runs 22 to 6.
    assert (values["tou_low_start"], values["tou_low_end"]) == (22, 6)
    assert values["tou_weekend"] == WEEKEND_LOW


def test_an_exception_without_days_leaves_the_weekend_alone() -> None:
    """Tensio charges the day rate on Saturdays and Sundays too."""
    values = prefill(_by_name("Tensio TS AS"))

    assert values["tou_rate_standard"] == pytest.approx(0.22102)
    assert values["tou_weekend"] == WEEKEND_LIKE_WEEKDAY


def test_a_flat_tariff_is_one_price_around_the_clock() -> None:
    values = prefill(_by_name("Lede AS"))

    assert values["tou_rate_low"] == values["tou_rate_standard"] == pytest.approx(0.1141)
    assert values["tou_low_start"] == values["tou_low_end"]


def test_a_seasonal_tariff_is_flagged_as_only_partly_prefilled() -> None:
    operator = _by_name("Sesongnett AS")

    assert operator.uncertain
    assert not _by_name("Elvia AS").uncertain
    # The first exception is cheaper than the base, so it becomes the low
    # window rather than the day rate.
    values = prefill(operator)
    assert values["tou_rate_low"] == pytest.approx(0.2489)
    assert (values["tou_low_start"], values["tou_low_end"]) == (22, 6)


class _Response:
    def __init__(self, text):
        self._text = text
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def text(self):
        return self._text


class _Session:
    def __init__(self, *texts):
        self.texts = list(texts)

    def get(self, url, headers=None, timeout=None):
        return _Response(self.texts.pop(0))


async def test_the_list_is_narrowed_to_the_users_price_area() -> None:
    areas = (
        '[{"country": "NO", "dsoCode": "7080005046220", "mba": "NO1"},'
        ' {"country": "NO", "dsoCode": "7080005051880", "mba": "NO3"},'
        ' {"country": "NO", "dsoCode": "7080005051279", "mba": "NO2"}]'
    )

    operators = await async_load_operators(_Session(SNAPSHOT, areas), "NO3")

    assert [operator.name for operator in operators] == ["Tensio TS AS"]


async def test_an_unknown_area_offers_every_operator() -> None:
    """A longer list is never a wrong one."""
    operators = await async_load_operators(_Session(SNAPSHOT, "not json"), "NO3")

    assert len(operators) == 4
