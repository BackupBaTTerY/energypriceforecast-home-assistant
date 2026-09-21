"""Norwegian grid tariffs, as a starting point for the user's own schedule.

Every Norwegian grid operator publishes its own prices, and most of them
differentiate the energy part by time of day: a day rate on weekdays, a
cheaper rate at night and at weekends. There is no official machine-readable
source. The volunteer project fri-nettleie collects them for all operators
in one generated snapshot, under CC-BY-4.0.

It is used here to *prefill*, not to price. Two measurements decided that:
its prices are stated without taxes, which is exactly what the formula
needs - but when this was written, half the entries had last been checked
more than six months earlier (median 274 days). Numbers that old may quietly
stop matching a bill, and a retail price that is silently wrong is worse
than one the user entered themselves. So the integration reads the snapshot
once, fills the fields in, shows when that operator was last checked, and
lets the user correct anything before saving.

Prices in the snapshot are in oere per kWh; everything here returns them in
kroner, the unit the price sensors use.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import VERSION
from .time_of_use import WEEKEND_LIKE_WEEKDAY, WEEKEND_LOW

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/kraftsystemet/fri-nettleie/main/"
    "docs/llm/llms.txt"
)
# Which price area each operator sits in, so a NO3 household is not offered
# the tariffs of NO1. Published by eSett, mirrored in the same project.
AREA_URL = (
    "https://raw.githubusercontent.com/kraftsystemet/fri-nettleie/main/"
    "referanse-data/esett/metering_grid_areas.json"
)
ATTRIBUTION = "fri-nettleie, CC-BY-4.0"

_HEADING = re.compile(r"^## (?P<name>.+)$")
_GLN = re.compile(r"^GLN: (?P<gln>[\d, ]+) \| Sist oppdatert: (?P<updated>[\d-]+)")
_GROUPS = re.compile(r"^Kundegrupper: (?P<groups>.+)$")
_BASE_RATE = re.compile(r"^Energiledd grunnpris: (?P<price>[\d.]+) ")
_EXCEPTION = re.compile(
    r"^\s+- (?P<name>[^:]+): (?P<price>[\d.]+) .*?"
    r"(?:\| timer (?P<from>\d+)-(?P<to>\d+))?"
    r"(?: \| dager: (?P<days>[^|]+))?"
    r"(?: \| måneder: (?P<months>[^|]+))?\s*$"
)

# The collection's own words for "Monday to Friday".
WEEKDAY_WORDS = ("virkedag", "ukedag")


@dataclass
class Operator:
    """One grid operator's household tariff, as far as it can be read."""

    name: str
    gln: str = ""
    updated: str = ""
    base_rate: float | None = None
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    household: bool = False

    @property
    def uncertain(self) -> bool:
        """Whether the shape is richer than the dialog can fill in.

        More than one exception, or one that only applies in some months:
        the fields are still prefilled from what fits, but the user has to
        look at their own price sheet.
        """
        return len(self.exceptions) > 1 or any(
            exception.get("months") for exception in self.exceptions
        )


def parse_snapshot(text: str) -> list[Operator]:
    """Every household operator in the snapshot, in the order it lists them."""
    operators: list[Operator] = []
    current: Operator | None = None
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = Operator(name=heading.group("name").strip())
            continue
        if current is None:
            continue
        gln = _GLN.match(line)
        if gln:
            current.gln = gln.group("gln").split(",")[0].strip()
            current.updated = gln.group("updated")
            # Only a section with a GLN is an operator; the rest of the
            # snapshot's headings explain the format.
            operators.append(current)
            continue
        groups = _GROUPS.match(line)
        if groups:
            current.household = "husholdning" in groups.group("groups")
            continue
        base = _BASE_RATE.match(line)
        if base:
            current.base_rate = float(base.group("price")) / 100
            continue
        exception = _EXCEPTION.match(line)
        if exception and current.base_rate is not None:
            current.exceptions.append(
                {
                    "price": float(exception.group("price")) / 100,
                    "from": int(exception.group("from") or 0),
                    # The snapshot reads "timer 6-21" as 06:00 up to 22:00.
                    "to": int(exception.group("to") or 0) + 1,
                    "days": (exception.group("days") or "").strip(),
                    "months": (exception.group("months") or "").strip(),
                }
            )
    return [
        operator
        for operator in operators
        if operator.household and operator.base_rate is not None
    ]


def prefill(operator: Operator) -> dict[str, Any]:
    """The schedule fields this operator suggests, in kroner per kWh.

    The snapshot names one base price and the periods that differ from it.
    Which of the two is the cheap one is decided by the prices themselves:
    most operators state the night rate as the base and the weekday daytime
    as the exception, but not all of them do.
    """
    base = operator.base_rate or 0.0
    values: dict[str, Any] = {
        "tou_low_start": 0,
        "tou_low_end": 0,
        "tou_peak_start": 0,
        "tou_peak_end": 0,
        "tou_weekend": WEEKEND_LIKE_WEEKDAY,
        "tou_rate_low": base,
        "tou_rate_standard": base,
        "tou_rate_peak": base,
    }
    if not operator.exceptions:
        return values
    exception = operator.exceptions[0]
    weekdays_only = any(word in exception["days"] for word in WEEKDAY_WORDS)
    if exception["price"] >= base:
        # The exception is the expensive daytime window, so everything else
        # - nights, and the weekend when the window is weekdays only - is
        # charged the base price.
        values["tou_rate_standard"] = exception["price"]
        values["tou_rate_peak"] = exception["price"]
        values["tou_low_start"] = exception["to"] % 24
        values["tou_low_end"] = exception["from"] % 24
        values["tou_weekend"] = WEEKEND_LOW if weekdays_only else WEEKEND_LIKE_WEEKDAY
    else:
        values["tou_rate_low"] = exception["price"]
        values["tou_low_start"] = exception["from"] % 24
        values["tou_low_end"] = exception["to"] % 24
    return values


async def _async_get(session: ClientSession, url: str) -> str:
    headers = {"User-Agent": f"EnergyPriceForecast-HomeAssistant/{VERSION}"}
    try:
        async with session.get(url, headers=headers, timeout=30) as response:
            response.raise_for_status()
            return await response.text()
    except (ClientError, ClientResponseError, TimeoutError) as err:
        raise CollectionUnavailable(str(err)) from err


class CollectionUnavailable(Exception):
    """The collected Norwegian tariffs could not be fetched."""


async def async_load_operators(
    session: ClientSession, market: str
) -> list[Operator]:
    """The operators of one price area, or of the whole country if unknown."""
    operators = parse_snapshot(await _async_get(session, SNAPSHOT_URL))
    try:
        areas = json.loads(await _async_get(session, AREA_URL))
    except (CollectionUnavailable, ValueError) as err:
        # Without the area list every operator is offered, which is a longer
        # list but never a wrong one.
        _LOGGER.debug("Norwegian price areas unavailable: %s", err)
        return operators
    by_gln = {
        str(area.get("dsoCode")): str(area.get("mba"))
        for area in areas
        if isinstance(area, dict) and area.get("country") == "NO"
    }
    in_area = [o for o in operators if by_gln.get(o.gln) == market.upper()]
    return in_area or operators
