"""Tests for the UI setup flow."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries, data_entry_flow

from custom_components.energypriceforecast.const import DOMAIN


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
            result["flow_id"],
            {
                "market": "DE",
                "horizon_hours": "48",
                "window_hours": 4,
                "api_key": "",
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Energy Price Forecast EU (DE)"
    assert result["data"]["horizon_hours"] == 48
    assert "api_key" not in result["data"]
