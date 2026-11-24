"""A Repairs notice while the published prices are late (1.23.0).

**Published prices missing** says that the price for the slot running now
is only a forecast. Until 1.21.0 there was nothing the owner of the
installation could do about that, which is why it was a problem flag and
nothing more. Since the own price source there is: Nord Pool and ENTSO-e
usually have the day-ahead prices shortly after the auction, even when our
freely licensed source is hours late, as it was for Germany on 20 and 24
November 2026. So while the prices are late and no source is set, this
module shows a notice under *Settings > Repairs* that says where to set one,
and removes it as soon as the published prices arrive or a source is set.

Not raised in the zones where the API never passes on exchange prices.
There the flag is on all the time, the own-source dialog already says what
a source does there, and a notice that never goes away teaches people to
ignore notices.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_MARKET,
    CONF_OWN_PRICE_ENTITY,
    DOMAIN,
    FORECAST_ONLY_MARKETS,
)

ISSUE_TRANSLATION_KEY = "late_prices"
LEARN_MORE_URL = (
    "https://github.com/BackupBaTTerY/energypriceforecast-home-assistant"
    "#your-own-price-source-1210"
)


def issue_id(entry_id: str) -> str:
    """One notice per configured market, so DK1 and DK2 each get their own."""
    return f"{ISSUE_TRANSLATION_KEY}_{entry_id}"


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def price_source_now(
    price_series: dict[str, Any] | None, summary: dict[str, Any] | None
) -> str | None:
    """Where the price for this moment comes from, as the flag reads it.

    The slot that strictly covers now, then the summary's own field - the
    same order as the binary sensor, so the notice and the flag agree.
    """
    entries = (price_series or {}).get("entries")
    if isinstance(entries, list):
        now = datetime.now(timezone.utc)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            start, end = _parse(entry.get("start")), _parse(entry.get("end"))
            if start is not None and end is not None and start <= now < end:
                source = entry.get("source")
                if isinstance(source, str) and source:
                    return source
                break
    flat = (summary or {}).get("flat") or {}
    source = flat.get("current_price_source")
    return source if isinstance(source, str) and source else None


def notice_wanted(market: str, own_price_entity: str | None, source: str | None) -> bool:
    """Whether the notice belongs on screen right now.

    Only the plain forecast counts: an early-auction price is a traded one,
    and `own_source` means a source is already doing the job. A source that
    is set but does not cover now is a matter for its own status in the
    diagnostics, not for advice to set one.
    """
    return (
        source == "forecast"
        and not own_price_entity
        and market not in FORECAST_ONLY_MARKETS
    )


@callback
def async_update_notice(
    hass: HomeAssistant,
    entry: ConfigEntry,
    price_series: dict[str, Any] | None,
    summary: dict[str, Any] | None,
) -> None:
    """Raise or clear the notice for one entry from the data just fetched."""
    market = entry.data[CONF_MARKET]
    source = price_source_now(price_series, summary)
    if notice_wanted(market, entry.options.get(CONF_OWN_PRICE_ENTITY), source):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id(entry.entry_id),
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_TRANSLATION_KEY,
            translation_placeholders={"market": market},
            learn_more_url=LEARN_MORE_URL,
        )
    else:
        async_clear_notice(hass, entry)


@callback
def async_clear_notice(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
