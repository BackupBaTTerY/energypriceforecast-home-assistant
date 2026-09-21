"""Tests for the Danish grid tariffs from Energi Data Service.

The records below have the shapes the live dataset has, measured when this
was written: TREFOR publishes one record per day, Radius one record for
months, Konstant books its discount as a separate record with negative
prices, and every operator publishes months ahead.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from aiohttp import ClientError

from custom_components.energypriceforecast.dk_datahub import (
    DatahubError,
    async_list_household_tariffs,
    discount_belongs_to,
    async_load_rates,
    parse_choice,
)

TODAY = date(2026, 9, 21)
TREFOR = "5790000392261"
RADIUS = "5790000705689"
KONSTANT = "5790000704842"


def _record(owner, gln, code, note, valid_from, valid_to, prices=None, resolution="PT1H"):
    record = {
        "ChargeOwner": owner,
        "GLN_Number": gln,
        "ChargeTypeCode": code,
        "Note": note,
        "ValidFrom": f"{valid_from}T00:00:00",
        "ValidTo": None if valid_to is None else f"{valid_to}T00:00:00",
        "ResolutionDuration": resolution,
    }
    for hour in range(24):
        record[f"Price{hour + 1}"] = None if prices is None else prices[hour]
    return record


def _day(night, day, peak):
    """A Tarifmodel 3.0 day: night 00-06, peak 17-21, day otherwise."""
    return [night if h < 6 else peak if 17 <= h < 21 else day for h in range(24)]


class _Response:
    def __init__(self, payload, status=200):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise ClientError(f"HTTP {self.status}")

    async def json(self, content_type=None):
        return self._payload


class _Session:
    """Answers each request with the next prepared response."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.requests.append(params or {})
        return self.responses.pop(0)


async def test_the_list_holds_every_household_tariff_in_force_today() -> None:
    session = _Session(
        _Response(
            {
                "records": [
                    # Next year's price, published ahead: not today's tariff.
                    _record("Radius Elnet A/S", RADIUS, "DT_C_01", "Nettarif C", "2027-01-01", None),
                    _record("TREFOR El-net A/S", TREFOR, "C", "Nettarif C", "2026-09-21", "2026-09-22"),
                    _record("Radius Elnet A/S", RADIUS, "DT_C_01", "Nettarif C", "2026-04-01", "2027-01-01"),
                    # A discount and a feed-in tariff are not what a household buys.
                    _record("Konstant Net A/S - 151", KONSTANT, "C_FBTNTR_R", "Rabat på Nettarif C", "2026-09-21", "2026-09-26"),
                    _record("Aal El-Net A M B A", "5790001095277", "CP", "Nettarif C Produktion", "2026-01-01", None),
                    # One price per day is no time-of-day tariff.
                    _record("Old Net", "5790000000001", "X", "Nettarif C", "2026-01-01", None, resolution="P1D"),
                    # Yesterday's TREFOR record: same tariff, listed once.
                    _record("TREFOR El-net A/S", TREFOR, "C", "Nettarif C", "2026-09-20", "2026-09-21"),
                ]
            }
        )
    )

    tariffs = await async_list_household_tariffs(session, TODAY)

    assert [t.label for t in tariffs] == [
        "Radius Elnet A/S - Nettarif C",
        "TREFOR El-net A/S - Nettarif C",
    ]
    assert tariffs[0].value == f"{RADIUS}|DT_C_01"
    request = session.requests[0]
    # Ending at tomorrow keeps next spring's prices out of the answer, and
    # filtering by the tariff's name keeps it to a megabyte instead of eighty.
    assert request["end"] == "2026-09-22"
    assert "Nettarif C" in json.loads(request["filter"])["Note"]


async def test_the_operators_discount_is_part_of_the_rate() -> None:
    """Konstant books its discount separately; the household pays the sum."""
    base = _day(0.0605, 0.0908, 0.2361)
    discount = _day(-0.0111, -0.0166, -0.0432)
    session = _Session(
        _Response(
            {
                "records": [
                    _record("Konstant", KONSTANT, "C_FBTNTR_B", "Nettarif C", "2026-09-21", "2026-09-26", base),
                    _record("Konstant", KONSTANT, "C_FBTNTR_R", "Rabat på Nettarif C - KONSTANT Net", "2026-09-21", "2026-09-26", discount),
                    # Its name contains "Nettarif C", but it belongs to the
                    # "lokal kollektiv" tariff. Matching by name applied it
                    # on top and took another 0.0182 off the evening peak.
                    _record("Konstant", KONSTANT, "C_FBTLKTFR", "Rabat Nettarif C lokal kollektiv", "2026-09-21", "2026-09-26", discount),
                ]
            }
        )
    )

    rates = await async_load_rates(session, f"{KONSTANT}|C_FBTNTR_B", TODAY, 1)

    assert rates[TODAY][3] == pytest.approx(0.0605 - 0.0111)
    assert rates[TODAY][18] == pytest.approx(0.2361 - 0.0432)
    assert rates[TODAY][12] == pytest.approx(0.0908 - 0.0166)


async def test_a_long_lived_record_covers_every_day_it_is_valid() -> None:
    """Radius publishes one record for months; each day reads from it."""
    prices = _day(0.1062, 0.1593, 0.4141)
    session = _Session(
        _Response(
            {"records": [_record("Radius", RADIUS, "DT_C_01", "Nettarif C", "2026-04-01", "2027-01-01", prices)]}
        )
    )

    rates = await async_load_rates(session, f"{RADIUS}|DT_C_01", TODAY, 2)

    assert sorted(rates) == [TODAY, date(2026, 9, 22), date(2026, 9, 23)]
    assert rates[date(2026, 9, 23)][18] == pytest.approx(0.4141)


async def test_a_day_without_a_record_is_left_out() -> None:
    """Better a visible gap than a day priced with another day's tariff."""
    session = _Session(
        _Response(
            {
                "records": [
                    _record("TREFOR", TREFOR, "C", "Nettarif C", "2026-09-21", "2026-09-22", _day(0.04, 0.06, 0.17)),
                ]
            }
        )
    )

    rates = await async_load_rates(session, f"{TREFOR}|C", TODAY, 2)

    assert list(rates) == [TODAY]


async def test_an_unknown_tariff_is_an_error() -> None:
    session = _Session(_Response({"records": []}))

    with pytest.raises(DatahubError):
        await async_load_rates(session, f"{TREFOR}|C", TODAY, 1)


async def test_being_rate_limited_is_an_error_not_a_crash() -> None:
    """The service answers 429 when pushed; that must not escape as anything else."""
    session = _Session(_Response({}, status=429))

    with pytest.raises(DatahubError):
        await async_list_household_tariffs(session, TODAY)


@pytest.mark.parametrize(
    ("discount", "tariff", "belongs"),
    [
        # Every pairing in force when this was written, from the live data.
        ("AAL-NTR05", "AAL-NT-05", True),
        ("C_FBTNTR_R", "C_FBTNTR_B", True),
        ("5RCFF", "5NCFF", True),
        ("SEF-NT-05R", "SEF-NT-05", True),
        ("30RE_C_ET", "30TR_C_ET", True),
        ("C_FBTLKTFR", "C_FBTLKTFB", True),
        # And the ones that must not pair up.
        ("C_FBTLKTFR", "C_FBTNTR_B", False),
        ("5RCFI", "5NCFF", False),
        ("30RE_AHJ", "30TR_C_ET", False),
        ("AAL-NTR10", "AAL-NT-05", False),
        ("TEV-NT-11R", "TEV-NT-01T", False),
    ],
)
def test_a_discount_is_matched_by_its_code(discount, tariff, belongs) -> None:
    """Names cannot tell - Cerius calls all its discounts the same."""
    assert discount_belongs_to(discount, tariff) is belongs


def test_the_stored_choice_reads_back() -> None:
    assert parse_choice(f"{RADIUS}|DT_C_01") == (RADIUS, "DT_C_01")
    assert parse_choice("") == ("", "")
