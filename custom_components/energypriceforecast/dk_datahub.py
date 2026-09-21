"""Danish network tariffs from Energi Data Service.

Denmark is the one market where a household can have its own grid tariff
without reading a PDF: Energinet publishes every grid operator's prices in
the DatahubPricelist dataset, hour by hour, months ahead, without a key. The
structure is the same everywhere (Tarifmodel 3.0), but the amounts change
three times a year, which is exactly why they should not be typed in by hand.

Three things about this dataset cost an afternoon each if you meet them in
production instead of here:

- **Sorting by ValidFrom without a bound shows the future first.** Operators
  publish months ahead, so the newest records are for next spring and an
  unfiltered query looks as if nothing is valid today. Every query here ends
  at the last day it needs.
- **``start``/``end`` select records by their validity, not by overlap.** A
  window query finds the operators who publish one record per day (TREFOR),
  and misses the ones whose record stays valid for months (Radius, Cerius,
  N1). Asking by the tariff's own name, newest first, finds both.
- **Some operators book their discount as a separate record** with negative
  hourly prices. What the household pays is the sum, and ignoring it
  overstates Konstant's evening network charge by about a fifth. Which
  discount belongs to which tariff is not in the data, and the names do not
  tell either: Konstant's "Rabat Nettarif C lokal kollektiv" contains
  "Nettarif C" but belongs to another tariff, and Cerius calls every one of
  its discounts "Rabat paa Cerius' nettarif". The codes do tell - see
  ``discount_belongs_to``.

The service answers HTTP 429 when pushed, so the integration asks once a day
for one tariff, and the setup dialog asks once for the list.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import VERSION

_LOGGER = logging.getLogger(__name__)

DATASET_URL = "https://api.energidataservice.dk/dataset/DatahubPricelist"

# D03 is a tariff, as opposed to a subscription (D01) or a fee (D02).
CHARGE_TYPE_TARIFF = "D03"

# What the operators call the household tariff. Low-voltage households are
# "C customers" in Danish grid language, and every operator's name for it
# starts that way; the list is what the dataset actually contains today.
# Filtering the query by these names is what keeps it to one megabyte
# instead of the eighty the whole tariff history weighs.
HOUSEHOLD_NOTES: tuple[str, ...] = (
    "Nettarif C",
    "Nettarif C time",
    "Nettarif C - tidsdifferentieret",
    "Nettarif C lokal kollektiv",
    "C-Kunde Nettarif",
)

# A household buys electricity; a production tariff is for feeding it in.
EXCLUDED_WORDS = ("produktion",)
DISCOUNT_WORD = "rabat"


class DatahubError(Exception):
    """Energi Data Service could not be reached or did not answer usefully."""


@dataclass(frozen=True)
class DatahubTariff:
    """One grid operator's household tariff, as offered in the dialog."""

    gln: str
    owner: str
    code: str
    note: str

    @property
    def value(self) -> str:
        """How the choice is stored in the config entry."""
        return f"{self.gln}|{self.code}"

    @property
    def label(self) -> str:
        return f"{self.owner} - {self.note}"


def parse_choice(value: str) -> tuple[str, str]:
    """The stored choice, back as (GLN, charge type code)."""
    gln, _, code = str(value).partition("|")
    return gln, code


def _hourly_prices(record: dict[str, Any]) -> list[float | None]:
    """Price1..Price24 as hour 0..23, without VAT."""
    prices: list[float | None] = []
    for hour in range(1, 25):
        value = record.get(f"Price{hour}")
        prices.append(float(value) if isinstance(value, (int, float)) else None)
    return prices


def _covers(record: dict[str, Any], day: date) -> bool:
    starts = str(record.get("ValidFrom") or "")[:10]
    ends = record.get("ValidTo")
    if not starts or starts > day.isoformat():
        return False
    return ends is None or str(ends)[:10] > day.isoformat()


def _is_household(note: str) -> bool:
    lowered = note.lower()
    if any(word in lowered for word in EXCLUDED_WORDS):
        return False
    return DISCOUNT_WORD not in lowered


def _edit_distance(first: str, second: str) -> int:
    previous = list(range(len(second) + 1))
    for i, a in enumerate(first, 1):
        current = [i]
        for j, b in enumerate(second, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b))
            )
        previous = current
    return previous[-1]


def discount_belongs_to(discount_code: str, tariff_code: str) -> bool:
    """Whether a discount record is booked against this tariff.

    Every operator that books discounts derives the discount's code from the
    tariff's: one character changed or added (Aal AAL-NT-05 / AAL-NTR05,
    Konstant C_FBTNTR_B / C_FBTNTR_R, Elvaerk 5NCFF / 5RCFF, Sunds SEF-NT-05
    / SEF-NT-05R), or only the first part of the code changed (Cerius
    30TR_C_ET / 30RE_C_ET). Checked against every discount record in force
    when this was written: each household tariff got one discount or none,
    and each one was its own.
    """
    alike = "".join(ch for ch in discount_code.upper() if ch.isalnum())
    other = "".join(ch for ch in tariff_code.upper() if ch.isalnum())
    if _edit_distance(alike, other) <= 1:
        return True
    parts, tariff_parts = discount_code.upper().split("_"), tariff_code.upper().split("_")
    return len(parts) >= 3 and len(parts) == len(tariff_parts) and parts[1:] == tariff_parts[1:]


async def _async_query(session: ClientSession, params: dict[str, str]) -> list[dict]:
    headers = {
        "Accept": "application/json",
        "User-Agent": f"EnergyPriceForecast-HomeAssistant/{VERSION}",
    }
    try:
        async with session.get(
            DATASET_URL, params=params, headers=headers, timeout=60
        ) as response:
            if response.status == 429:
                raise DatahubError(
                    "Energi Data Service is rate limiting us; try again later."
                )
            response.raise_for_status()
            payload = await response.json(content_type=None)
    except DatahubError:
        raise
    except (ClientError, ClientResponseError, TimeoutError) as err:
        raise DatahubError(str(err)) from err
    except ValueError as err:
        raise DatahubError("Energi Data Service did not return JSON.") from err
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise DatahubError("Energi Data Service returned no records.")
    return records


async def async_list_household_tariffs(
    session: ClientSession, today: date
) -> list[DatahubTariff]:
    """Every operator's household tariff that is in force today.

    One query, about a megabyte: asking by the tariff's name and ending at
    tomorrow keeps both the eighty-megabyte history and next spring's prices
    out of the answer, and still reaches every operator.
    """
    records = await _async_query(
        session,
        {
            "limit": "5000",
            "columns": "ChargeOwner,GLN_Number,ChargeTypeCode,Note,ValidFrom,"
            "ValidTo,ResolutionDuration",
            "filter": json.dumps(
                {
                    "ChargeType": [CHARGE_TYPE_TARIFF],
                    "Note": list(HOUSEHOLD_NOTES),
                }
            ),
            "end": (today + timedelta(days=1)).isoformat(),
            "sort": "ValidFrom DESC",
        },
    )
    tariffs: dict[str, DatahubTariff] = {}
    for record in records:
        note = str(record.get("Note") or "")
        if record.get("ResolutionDuration") != "PT1H" or not _is_household(note):
            continue
        if not _covers(record, today):
            continue
        tariff = DatahubTariff(
            gln=str(record.get("GLN_Number") or ""),
            owner=str(record.get("ChargeOwner") or "").strip(),
            code=str(record.get("ChargeTypeCode") or ""),
            note=note.strip(),
        )
        if tariff.gln and tariff.code:
            tariffs.setdefault(tariff.value, tariff)
    return sorted(tariffs.values(), key=lambda item: (item.owner, item.note))


async def async_load_rates(
    session: ClientSession, choice: str, first_day: date, days: int
) -> dict[date, tuple[float | None, ...]]:
    """The hourly network charge of one tariff, for the days a forecast covers.

    Any discount the operator books separately is added, because the sum is
    what the household pays. Days the dataset does not cover are left out:
    the retail price then has a visible gap rather than a rate from last week.
    """
    gln, code = parse_choice(choice)
    if not gln or not code:
        raise DatahubError("No Danish grid tariff was selected.")
    last_day = first_day + timedelta(days=max(days, 1))
    records = await _async_query(
        session,
        {
            "limit": "1000",
            "columns": "ChargeTypeCode,Note,ValidFrom,ValidTo,"
            + ",".join(f"Price{hour}" for hour in range(1, 25)),
            "filter": json.dumps(
                {"GLN_Number": [gln], "ChargeType": [CHARGE_TYPE_TARIFF]}
            ),
            "end": (last_day + timedelta(days=1)).isoformat(),
            "sort": "ValidFrom DESC",
        },
    )
    chosen = [r for r in records if r.get("ChargeTypeCode") == code]
    if not chosen:
        raise DatahubError(f"Energi Data Service knows no tariff {code} for {gln}.")
    discounts = [
        r
        for r in records
        if DISCOUNT_WORD in str(r.get("Note") or "").lower()
        and discount_belongs_to(str(r.get("ChargeTypeCode") or ""), code)
    ]

    rates: dict[date, tuple[float | None, ...]] = {}
    day = first_day
    while day <= last_day:
        record = next((r for r in chosen if _covers(r, day)), None)
        if record is None:
            day += timedelta(days=1)
            continue
        prices = _hourly_prices(record)
        for discount in discounts:
            if not _covers(discount, day):
                continue
            off = _hourly_prices(discount)
            prices = [
                None if price is None or off[hour] is None else price + off[hour]
                for hour, price in enumerate(prices)
            ]
        rates[day] = tuple(prices)
        day += timedelta(days=1)
    if not rates:
        raise DatahubError("Energi Data Service has no prices for the days ahead.")
    return rates
