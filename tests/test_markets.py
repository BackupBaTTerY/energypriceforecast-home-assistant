"""Every market the dialog offers must be usable end to end.

Only constants and the zone database are read, so this runs without the
pytest-homeassistant-custom-component plugin.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from custom_components.energypriceforecast.const import (
    DEFAULT_TIME_ZONE,
    MARKETS,
    MARKET_TIME_ZONES,
)

# Added in 1.8.0, after the API started serving them.
NEW_IN_1_8 = ("BG", "ES", "GR", "PT", "RO", "SK")


@pytest.mark.parametrize("market", NEW_IN_1_8)
def test_the_markets_added_in_1_8_are_offered(market) -> None:
    assert market in MARKETS


def test_every_market_has_its_own_clock() -> None:
    """A tariff window is stated in the market's local time.

    Without an entry a market falls back to Central European Time, which is
    an hour wrong for Portugal and an hour wrong the other way for Finland,
    Greece, Bulgaria and Romania - and nothing would say so.
    """
    missing = sorted(set(MARKETS) - set(MARKET_TIME_ZONES))

    assert not missing, f"no time zone for {missing}"


def test_no_time_zone_for_a_market_that_does_not_exist() -> None:
    assert not sorted(set(MARKET_TIME_ZONES) - set(MARKETS))


@pytest.mark.parametrize("zone", sorted({*MARKET_TIME_ZONES.values(), DEFAULT_TIME_ZONE}))
def test_every_zone_is_a_real_one(zone) -> None:
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError as err:  # pragma: no cover - only on a broken tzdata
        pytest.fail(f"{zone}: {err}")


@pytest.mark.parametrize(
    ("market", "zone"),
    [
        # The four that are not on Central European Time, as the API reports
        # them - these are the ones a wrong default would silently shift.
        ("PT", "Europe/Lisbon"),
        ("GR", "Europe/Athens"),
        ("BG", "Europe/Sofia"),
        ("RO", "Europe/Bucharest"),
        ("FI", "Europe/Helsinki"),
        ("ES", "Europe/Madrid"),
        ("SK", "Europe/Bratislava"),
    ],
)
def test_the_zones_that_are_easy_to_get_wrong(market, zone) -> None:
    assert MARKET_TIME_ZONES[market] == zone
