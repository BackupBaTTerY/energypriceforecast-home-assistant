"""Tests for the UI setup flow."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow

from custom_components.energypriceforecast.api import (
    EnergyPriceForecastAuthError,
    EnergyPriceForecastRetailUnavailable,
)
from custom_components.energypriceforecast.config_flow import (
    _schema,
    _validate_cheapest_hours_selection,
    _validate_retail_selection,
)
from custom_components.energypriceforecast import async_migrate_entry
from custom_components.energypriceforecast.const import (
    CONF_CHEAPEST_HOURS_COUNT,
    CONF_CHEAPEST_HOURS_WINDOW_HOURS,
    CONF_HORIZON_HOURS,
    CONF_MARKET,
    CONF_POSTAL_CODE,
    CONF_RETAIL_PRICING,
    DOMAIN,
    HORIZON_HOURS_OPTIONS,
    MAX_HORIZON_HOURS,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

BASE_USER_INPUT = {
    "market": "DE",
    "horizon_hours": "48",
    "window_hours": 4,
    "api_key": "",
    "retail_pricing": False,
    "postal_code": "",
    "update_interval_minutes": 30,
    "cheapest_hours_count": 0,
}


async def test_user_flow(hass) -> None:
    """A valid market creates a config entry."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is data_entry_flow.FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BASE_USER_INPUT
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Energy Price Forecast EU (DE)"
    assert result["data"]["horizon_hours"] == 48
    assert "api_key" not in result["data"]
    assert result["data"]["retail_pricing"] is False
    assert result["data"]["update_interval_minutes"] == 30
    assert result["data"]["cheapest_hours_count"] == 0


async def test_user_flow_rejects_retail_pricing_for_unsupported_market(hass) -> None:
    """Retail pricing on an unsupported market fails before any API call."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**BASE_USER_INPUT, "market": "BE", "retail_pricing": True},
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "retail_not_supported"
    mock_validate.assert_not_called()


async def test_user_flow_requires_postal_code_for_german_retail_pricing(hass) -> None:
    """Germany needs a postal code before retail pricing can be validated."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**BASE_USER_INPUT, "market": "DE", "retail_pricing": True},
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "postal_code_required"
    mock_validate.assert_not_called()


async def test_user_flow_rejects_malformed_postal_code(hass) -> None:
    """A postal code that is not 5 digits fails validation locally."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **BASE_USER_INPUT,
                "market": "DE",
                "retail_pricing": True,
                "postal_code": "abc",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_postal_code"
    mock_validate.assert_not_called()


async def test_user_flow_surfaces_retail_unavailable_error(hass) -> None:
    """A retail-probe failure from the API maps to its own error message."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(side_effect=EnergyPriceForecastRetailUnavailable("boom")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **BASE_USER_INPUT,
                "market": "DE",
                "retail_pricing": True,
                "postal_code": "10115",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "retail_unavailable"


async def test_user_flow_surfaces_auth_error(hass) -> None:
    """A rejected API key maps to invalid_auth."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(side_effect=EnergyPriceForecastAuthError("boom")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**BASE_USER_INPUT, "api_key": "some-key-123456"}
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"


async def test_user_flow_aborts_on_duplicate_market(hass) -> None:
    """The same market cannot be configured twice."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], BASE_USER_INPUT
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BASE_USER_INPUT
        )

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def test_withdrawn_horizon_does_not_break_the_form() -> None:
    """A stored 168 must render as a valid option, not raise.

    168 was offered until the forecast was confirmed to never exceed
    MAX_HORIZON_HOURS. Entries are migrated on load, but voluptuous
    validates a Required field's default whenever the key is missing from
    the input, so a stale entry reaching the form must still degrade to the
    nearest offered horizon instead of raising.
    """
    schema = _schema({CONF_HORIZON_HOURS: 168})

    validated = schema({})

    assert validated[CONF_HORIZON_HOURS] == str(MAX_HORIZON_HOURS)


def test_horizon_options_stay_within_the_forecast_ceiling() -> None:
    """Nothing may be offered that the forecast cannot actually deliver."""
    assert max(HORIZON_HOURS_OPTIONS) == MAX_HORIZON_HOURS


async def test_migration_clamps_a_withdrawn_horizon(hass) -> None:
    """An existing entry on 168 is rewritten to the real ceiling on load."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="DE",
        data={**BASE_USER_INPUT, CONF_HORIZON_HOURS: 168},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.data[CONF_HORIZON_HOURS] == MAX_HORIZON_HOURS
    assert entry.version == 2


async def test_migration_leaves_a_valid_horizon_alone(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="NL",
        data={**BASE_USER_INPUT, CONF_HORIZON_HOURS: 48},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.data[CONF_HORIZON_HOURS] == 48
    assert entry.version == 2


def test_schema_default_horizon_survives_validation_when_stored_as_int() -> None:
    """A previously-stored int horizon_hours must not crash schema validation.

    _normalize_input() stores horizon_hours as an int, and reconfigure
    seeds _schema()'s defaults straight from the stored config entry -
    but horizon_hours is a SelectSelector whose options are strings.
    voluptuous substitutes and validates a Required field's default
    whenever the key is missing from the input, so an int default there
    used to fail SelectSelector's strict internal str check.
    """
    schema = _schema({"horizon_hours": 48})

    validated = schema({})

    assert validated["horizon_hours"] == "48"


@pytest.mark.parametrize(
    ("data", "expected_error"),
    [
        ({CONF_MARKET: "DE", CONF_RETAIL_PRICING: False, CONF_POSTAL_CODE: None}, None),
        ({CONF_MARKET: "BE", CONF_RETAIL_PRICING: True, CONF_POSTAL_CODE: None}, "retail_not_supported"),
        ({CONF_MARKET: "DE", CONF_RETAIL_PRICING: True, CONF_POSTAL_CODE: None}, "postal_code_required"),
        ({CONF_MARKET: "DE", CONF_RETAIL_PRICING: True, CONF_POSTAL_CODE: "1234"}, "invalid_postal_code"),
        ({CONF_MARKET: "DE", CONF_RETAIL_PRICING: True, CONF_POSTAL_CODE: "10115"}, None),
        ({CONF_MARKET: "NL", CONF_RETAIL_PRICING: True, CONF_POSTAL_CODE: None}, None),
    ],
)
def test_validate_retail_selection(data, expected_error) -> None:
    """The synchronous pre-check matches each documented rule."""
    assert _validate_retail_selection(data) == expected_error


@pytest.mark.parametrize(
    ("count", "window_hours", "expected_error"),
    [
        (0, 24, None),
        (12, 24, None),
        (24, 24, None),
        (25, 24, "cheapest_hours_exceeds_window"),
        (48, 24, "cheapest_hours_exceeds_window"),
    ],
)
def test_validate_cheapest_hours_selection(count, window_hours, expected_error) -> None:
    """N (count) must never exceed X (the block length)."""
    data = {
        CONF_CHEAPEST_HOURS_COUNT: count,
        CONF_CHEAPEST_HOURS_WINDOW_HOURS: window_hours,
    }
    assert _validate_cheapest_hours_selection(data) == expected_error


async def test_user_flow_rejects_cheapest_hours_count_above_window(hass) -> None:
    """Picking more cheapest hours than the block is long fails before any API call."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **BASE_USER_INPUT,
                "cheapest_hours_count": 30,
                "cheapest_hours_window_hours": 24,
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "cheapest_hours_exceeds_window"
    mock_validate.assert_not_called()


async def test_user_flow_accepts_weekend_and_block_settings(hass) -> None:
    """The new block/weekend fields normalize to ints and get stored."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **BASE_USER_INPUT,
                "cheapest_hours_count": 24,
                "cheapest_hours_window_hours": 48,
                "cheapest_hours_start_hour": 6,
                "weekend_hours_count": 8,
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["cheapest_hours_window_hours"] == 48
    assert result["data"]["cheapest_hours_start_hour"] == 6
    assert result["data"]["weekend_hours_count"] == 8


@pytest.mark.parametrize(
    ("greenest_count", "window_hours", "expected_error"),
    [
        (0, 24, None),
        (24, 24, None),
        (25, 24, "greenest_hours_exceeds_window"),
    ],
)
def test_validate_greenest_hours_selection(
    greenest_count, window_hours, expected_error
) -> None:
    """The CO2 plan shares the block, so it shares the block's ceiling."""
    data = {
        CONF_CHEAPEST_HOURS_COUNT: 0,
        CONF_CHEAPEST_HOURS_WINDOW_HOURS: window_hours,
        "greenest_hours_count": greenest_count,
    }
    assert _validate_cheapest_hours_selection(data) == expected_error


def test_a_missing_greenest_count_is_not_an_error() -> None:
    """Entries saved before 1.2.0 have no such key, and that means "off"."""
    data = {
        CONF_CHEAPEST_HOURS_COUNT: 4,
        CONF_CHEAPEST_HOURS_WINDOW_HOURS: 24,
    }
    assert _validate_cheapest_hours_selection(data) is None
