"""The Repairs notice while the published prices are late (1.23.0)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir

from custom_components.energypriceforecast.const import DOMAIN
from custom_components.energypriceforecast.late_prices import (
    issue_id,
    notice_wanted,
    price_source_now,
)

from .test_sensor import NOW, _setup_entry, _slot


@pytest.mark.parametrize(
    ("market", "own", "source", "wanted"),
    [
        # Germany on 24 November 2026: the day was missing, nothing set.
        ("DE", None, "forecast", True),
        # A source is set: nothing to advise, even if it does not cover now.
        ("DE", "sensor.nord_pool_de", "forecast", False),
        # The source already covers now.
        ("DE", "sensor.nord_pool_de", "own_source", False),
        # The ordinary case.
        ("DE", None, "day_ahead", False),
        # Traded, if not official - and far closer than the model.
        ("NL", None, "early_auction", False),
        # Always a forecast there; a notice that never goes away is noise.
        ("HR", None, "forecast", False),
        ("SE1", None, "forecast", False),
        # Unknown provenance is no reason to advise anything.
        ("DE", None, None, False),
    ],
)
def test_when_the_notice_is_wanted(market, own, source, wanted) -> None:
    assert notice_wanted(market, own, source) is wanted


def test_the_source_now_is_the_covering_slot_then_the_summary() -> None:
    series = {
        "entries": [
            {"start": "2026-11-24T10:00:00Z", "end": "2026-11-24T10:15:00Z",
             "source": "day_ahead"},
            {"start": "2026-11-24T10:15:00Z", "end": "2026-11-24T10:30:00Z",
             "source": "forecast"},
        ]
    }
    summary = {"flat": {"current_price_source": "day_ahead"}}
    with patch(
        "custom_components.energypriceforecast.late_prices.datetime"
    ) as clock:
        clock.now.return_value = datetime(2026, 11, 24, 10, 20, tzinfo=timezone.utc)
        clock.fromisoformat = datetime.fromisoformat
        assert price_source_now(series, summary) == "forecast"
        # No slot covers 11:00: the summary answers, as for the flag.
        clock.now.return_value = datetime(2026, 11, 24, 11, 0, tzinfo=timezone.utc)
        assert price_source_now(series, summary) == "day_ahead"
        assert price_source_now(None, None) is None


async def test_late_prices_raise_the_notice_and_unloading_clears_it(
    hass, freezer
) -> None:
    freezer.move_to(NOW)
    entry = await _setup_entry(
        hass,
        price_entries=_slot("forecast"),
        price_series_source={"published_until": None},
    )
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, issue_id(entry.entry_id))

    assert issue is not None
    assert issue.translation_key == "late_prices"
    assert issue.translation_placeholders == {"market": "DE"}
    assert issue.is_fixable is False

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, issue_id(entry.entry_id)) is None


async def test_published_prices_raise_no_notice(hass, freezer) -> None:
    freezer.move_to(NOW)
    entry = await _setup_entry(
        hass,
        price_entries=_slot("day_ahead"),
        price_series_source={"published_until": "2026-10-10T22:00:00.000Z"},
    )

    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id)) is None
