"""Tests for the UI setup flow."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow

from custom_components.energypriceforecast.api import (
    EnergyPriceForecastAuthError,
    EnergyPriceForecastRetailUnavailable,
)
from custom_components.energypriceforecast.config_flow import (
    _validate_retail_selection,
)
from custom_components.energypriceforecast.const import (
    CONF_MARKET,
    CONF_POSTAL_CODE,
    CONF_RETAIL_PRICING,
    DOMAIN,
)

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
