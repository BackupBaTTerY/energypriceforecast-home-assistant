"""Retail price from the user's own formula: day-ahead x factor + surcharge.

The API's retail estimate already has this shape - (day-ahead + surcharges)
x (1 + VAT) - but with numbers the project has to keep current for every
market it supports. The formula leaves the numbers to the user, so any market
can have a retail price. It is applied here, to the base series and summary
the integration fetches anyway, which is why it needs no request of its own.

A positive factor and a constant surcharge keep the order of the hours: the
cheapest window and the plans pick the same hours as on the day-ahead price.
What changes are the amounts and the savings, which then match the bill.

A ``tariff`` - a grid charge that depends on the time of day, see
time_of_use.py - is the one thing that does reorder them, and it is meant
to: a cheap night rate can make a night hour the cheapest of the day even
where the exchange price is not. It sits inside the factor because grid
charges, like the day-ahead price, are published without VAT.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .time_of_use import TariffSource, mean_rate

# The flat summary fields that carry an amount, and the window each one is
# the average of - None means "right now". Everything else in the block -
# window start and end, status, "active now" - describes hours, and a
# positive factor with a constant surcharge leaves the hours where they are.
PRICE_FLAT_FIELDS: dict[str, tuple[str, str] | None] = {
    "current_price": None,
    "cheapest_window_avg_price": ("cheapest_window_start", "cheapest_window_end"),
    "best_price_window_avg_price": (
        "best_price_window_start",
        "best_price_window_end",
    ),
}


def _moment(value: Any) -> datetime | None:
    """One ISO timestamp from the API, or None if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

# The API rounds its retail prices to six decimals too; without it a state
# would read 0.30000000000000004.
_DIGITS = 6


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def retail_value(
    spot: float, factor: float, surcharge: float, tariff: float = 0.0
) -> float:
    """One slot's retail price."""
    return round(factor * (spot + tariff) + surcharge, _DIGITS)


def apply_to_series(
    series: dict[str, Any] | None,
    factor: float,
    surcharge: float,
    tariff: TariffSource | None = None,
) -> dict[str, Any] | None:
    """The price series with every value turned into its retail price.

    Everything else - unit, source, slot boundaries - is kept, so the result
    has exactly the shape of a retail series fetched from the API.

    With a time-of-day ``tariff``, a slot the tariff cannot price is left out
    rather than priced without it. A missing hour is visible - the plans
    refuse to cover a block with a gap - while an hour silently short of its
    network charge would quietly move a plan into the most expensive hours.
    """
    if not series:
        return None
    entries = []
    for entry in series.get("entries") or []:
        if not _is_number(entry.get("value")):
            entries.append(entry)
            continue
        rate = 0.0
        if tariff is not None:
            moment = _moment(entry.get("start"))
            rate = tariff.rate_at(moment) if moment is not None else None
            if rate is None:
                continue
        entries.append(
            {
                **entry,
                "value": retail_value(float(entry["value"]), factor, surcharge, rate),
            }
        )
    return {**series, "entries": entries}


def apply_to_summary(
    summary: dict[str, Any] | None,
    factor: float,
    surcharge: float,
    tariff: TariffSource | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """The summary with its flat price amounts turned into retail prices.

    Each amount gets the network charge of the hours it covers: the current
    price the rate of this moment, a window average the average rate across
    that window. Where the tariff has no rate, the amount is cleared instead
    of being reported too low.
    """
    if not summary:
        return None
    flat = dict(summary.get("flat") or {})
    for field, window in PRICE_FLAT_FIELDS.items():
        if not _is_number(flat.get(field)):
            continue
        rate: float | None = 0.0
        if tariff is not None:
            if window is None:
                rate = tariff.rate_at(now) if now is not None else None
            else:
                start, end = (_moment(flat.get(name)) for name in window)
                rate = (
                    mean_rate(tariff, start, end)
                    if start is not None and end is not None
                    else None
                )
        flat[field] = (
            None
            if rate is None
            else retail_value(float(flat[field]), factor, surcharge, rate)
        )
    return {**summary, "flat": flat}


def plan_basis(factor: float, surcharge: float, tariff: TariffSource | None = None) -> str:
    """The part of a stored plan's key that names the prices it was built on.

    A stored plan keeps its averages in the prices it was built on, so a plan
    priced with one formula - or with one network tariff - must not be shown
    after either of them changed.
    """
    basis = f"formula|{factor:g}|{surcharge:g}"
    return f"{basis}|{tariff.key()}" if tariff is not None else basis
