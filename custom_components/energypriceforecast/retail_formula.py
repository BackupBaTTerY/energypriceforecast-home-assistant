"""Retail price from the user's own formula: day-ahead x factor + surcharge.

The API's retail estimate already has this shape - (day-ahead + surcharges)
x (1 + VAT) - but with numbers the project has to keep current for every
market it supports. The formula leaves the numbers to the user, so any market
can have a retail price. It is applied here, to the base series and summary
the integration fetches anyway, which is why it needs no request of its own.

A positive factor and a constant surcharge keep the order of the hours: the
cheapest window and the plans pick the same hours as on the day-ahead price.
What changes are the amounts and the savings, which then match the bill.

``tariff`` is the hook for grid charges that depend on the time of day, such
as the Danish Tarifmodel 3.0: the charge of the slot's hour, excluding VAT
like the day-ahead price, which is why it sits inside the factor. Nothing
supplies it yet, so it is always 0.
"""

from __future__ import annotations

from typing import Any

# The flat summary fields that carry an amount. Everything else in the block
# - window start and end, status, "active now" - describes hours, and a
# positive factor with a constant surcharge leaves the hours where they are.
PRICE_FLAT_FIELDS = (
    "current_price",
    "cheapest_window_avg_price",
    "best_price_window_avg_price",
)

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
    series: dict[str, Any] | None, factor: float, surcharge: float
) -> dict[str, Any] | None:
    """The price series with every value turned into its retail price.

    Everything else - unit, source, slot boundaries - is kept, so the result
    has exactly the shape of a retail series fetched from the API.
    """
    if not series:
        return None
    entries = [
        {**entry, "value": retail_value(float(entry["value"]), factor, surcharge)}
        if _is_number(entry.get("value"))
        else entry
        for entry in series.get("entries") or []
    ]
    return {**series, "entries": entries}


def apply_to_summary(
    summary: dict[str, Any] | None, factor: float, surcharge: float
) -> dict[str, Any] | None:
    """The summary with its flat price amounts turned into retail prices."""
    if not summary:
        return None
    flat = dict(summary.get("flat") or {})
    for field in PRICE_FLAT_FIELDS:
        if _is_number(flat.get(field)):
            flat[field] = retail_value(float(flat[field]), factor, surcharge)
    return {**summary, "flat": flat}


def plan_basis(factor: float, surcharge: float) -> str:
    """The part of a stored plan's key that names the formula it was priced in.

    A stored plan keeps its averages in the prices it was built on, so a plan
    priced with one formula must not be shown after the formula changed.
    """
    return f"formula|{factor:g}|{surcharge:g}"
