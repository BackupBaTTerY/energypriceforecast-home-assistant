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
    CONF_RETAIL_FACTOR,
    CONF_RETAIL_SOURCE,
    DOMAIN,
    HORIZON_HOURS_OPTIONS,
    MAX_HORIZON_HOURS,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.energypriceforecast.dk_datahub import (
    DatahubError,
    DatahubTariff,
)
from custom_components.energypriceforecast.no_frinettleie import parse_snapshot

BASE_USER_INPUT = {
    "market": "DE",
    "horizon_hours": "48",
    "window_hours": 4,
    "api_key": "",
    "retail_source": "off",
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
    assert result["data"]["retail_source"] == "off"
    assert "retail_pricing" not in result["data"]
    assert result["data"]["update_interval_minutes"] == 30
    assert result["data"]["cheapest_hours_count"] == 0


async def test_user_flow_rejects_the_estimate_for_unsupported_market(hass) -> None:
    """The estimate on an unsupported market fails before any API call."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**BASE_USER_INPUT, "market": "BE", "retail_source": "estimate"},
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "retail_not_supported"
    mock_validate.assert_not_called()


async def test_user_flow_accepts_an_own_formula_in_any_market(hass) -> None:
    """The formula needs no estimate, so Belgium can have a retail price too."""
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
                "market": "BE",
                "retail_source": "formula",
                "retail_factor": 1.21,
                "retail_surcharge": 0.02,
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["retail_source"] == "formula"
    assert result["data"]["retail_factor"] == pytest.approx(1.21)
    assert result["data"]["retail_surcharge"] == pytest.approx(0.02)
    # No postal code needed: the formula does not look up any grid fee.
    assert "postal_code" not in result["data"]


async def test_user_flow_requires_postal_code_for_the_german_estimate(hass) -> None:
    """Germany needs a postal code before the estimate can be validated."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**BASE_USER_INPUT, "market": "DE", "retail_source": "estimate"},
        )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "postal_code_required"
    mock_validate.assert_not_called()


async def test_reconfigure_drops_the_old_retail_checkbox(hass) -> None:
    """The first save in 1.6.0 stores the source alone, not both."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id="DE",
        data={
            **BASE_USER_INPUT,
            "horizon_hours": 48,
            "retail_pricing": True,
            "retail_source": "estimate",
            "postal_code": "10115",
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.energypriceforecast.config_flow._validate_input",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.energypriceforecast.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **BASE_USER_INPUT,
                "retail_source": "formula",
                "retail_factor": 1.19,
                "retail_surcharge": 0.25,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is data_entry_flow.FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["retail_source"] == "formula"
    assert entry.data["retail_factor"] == pytest.approx(1.19)
    assert "retail_pricing" not in entry.data


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
                "retail_source": "estimate",
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
                "retail_source": "estimate",
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
    assert entry.version == 3


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
    assert entry.version == 3


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(
    ("stored", "expected_source"),
    [({"retail_pricing": True}, "estimate"), ({"retail_pricing": False}, "off"), ({}, "off")],
)
async def test_migration_turns_the_retail_checkbox_into_a_source(
    hass, version, stored, expected_source
) -> None:
    """A ticked box always meant the estimate, and that is what it becomes.

    Anything else would silently change the prices of the users who had it on,
    or give a retail price to those who never asked for one.
    """
    old = {k: v for k, v in BASE_USER_INPUT.items() if k != "retail_source"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=version,
        unique_id="DE",
        data={**old, "postal_code": "10115", **stored},
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 3
    assert entry.data["retail_source"] == expected_source
    # The box stays until the next reconfigure, so a rollback to 1.5.x
    # still finds it; nothing in 1.6.0 reads it.
    assert entry.data.get("retail_pricing") == stored.get("retail_pricing")
    # The postal code belongs to the estimate and stays with it.
    assert entry.data["postal_code"] == "10115"


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
        ({CONF_MARKET: "DE", CONF_RETAIL_SOURCE: "off", CONF_POSTAL_CODE: None}, None),
        ({CONF_MARKET: "BE", CONF_RETAIL_SOURCE: "estimate", CONF_POSTAL_CODE: None}, "retail_not_supported"),
        ({CONF_MARKET: "DE", CONF_RETAIL_SOURCE: "estimate", CONF_POSTAL_CODE: None}, "postal_code_required"),
        ({CONF_MARKET: "DE", CONF_RETAIL_SOURCE: "estimate", CONF_POSTAL_CODE: "1234"}, "invalid_postal_code"),
        ({CONF_MARKET: "DE", CONF_RETAIL_SOURCE: "estimate", CONF_POSTAL_CODE: "10115"}, None),
        ({CONF_MARKET: "NL", CONF_RETAIL_SOURCE: "estimate", CONF_POSTAL_CODE: None}, None),
        # The formula works everywhere and needs no postal code, not even in DE.
        ({CONF_MARKET: "BE", CONF_RETAIL_SOURCE: "formula", CONF_RETAIL_FACTOR: 1.21}, None),
        ({CONF_MARKET: "DE", CONF_RETAIL_SOURCE: "formula", CONF_RETAIL_FACTOR: 1.19, CONF_POSTAL_CODE: None}, None),
        ({CONF_MARKET: "CH", CONF_RETAIL_SOURCE: "formula", CONF_RETAIL_FACTOR: 0.0}, "invalid_retail_factor"),
        ({CONF_MARKET: "NL", CONF_RETAIL_SOURCE: "formula", CONF_RETAIL_FACTOR: -1.21}, "invalid_retail_factor"),
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


async def test_user_flow_stores_the_local_currency_choice(hass) -> None:
    """The checkbox is saved on the entry as a plain boolean."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**BASE_USER_INPUT, "market": "CZ", "local_currency": True},
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["local_currency"] is True


async def test_local_currency_is_off_unless_ticked(hass) -> None:
    """Left alone, every market keeps the currency it had before."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], BASE_USER_INPUT
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["local_currency"] is False


# The formula a network tariff needs, in Germany, with a tariff to enter.
TARIFF_INPUT = {
    **BASE_USER_INPUT,
    "retail_source": "formula",
    "retail_factor": 1.19,
    "retail_surcharge": 0.1,
    "tou_source": "manual",
}
DANISH_WINDOWS = {
    "tou_low_start": 0,
    "tou_low_end": 6,
    "tou_peak_start": 17,
    "tou_peak_end": 21,
    "tou_weekend": "like_weekday",
    "tou_winter": True,
    "tou_winter_from": 10,
    "tou_winter_to": 3,
}


def _fields(result) -> set[str]:
    return {str(key) for key in result["data_schema"].schema}


async def test_a_manual_tariff_is_asked_in_two_more_steps(hass) -> None:
    """Windows first, then exactly the amounts those windows use."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TARIFF_INPUT
        )
        assert result["step_id"] == "tariff_windows"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], DANISH_WINDOWS
        )
        assert result["step_id"] == "tariff_rates"
        # A peak window and winter prices: all six amounts are asked for.
        assert _fields(result) == {
            "tou_rate_low",
            "tou_rate_standard",
            "tou_rate_peak",
            "tou_winter_rate_low",
            "tou_winter_rate_standard",
            "tou_winter_rate_peak",
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "tou_rate_low": 0.0429,
                "tou_rate_standard": 0.0644,
                "tou_rate_peak": 0.1674,
                "tou_winter_rate_low": 0.0653,
                "tou_winter_rate_standard": 0.1959,
                "tou_winter_rate_peak": 0.5877,
            },
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["tou_source"] == "manual"
    assert (data["tou_low_start"], data["tou_low_end"]) == (0, 6)
    assert data["tou_winter"] is True
    assert data["tou_winter_rate_peak"] == pytest.approx(0.5877)
    assert "tou_tariff" not in data


async def test_a_two_level_tariff_asks_for_two_amounts(hass) -> None:
    """No peak window and no winter: the form does not ask for them."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TARIFF_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **DANISH_WINDOWS,
                "tou_low_start": 22,
                "tou_low_end": 6,
                "tou_peak_start": 0,
                "tou_peak_end": 0,
                "tou_weekend": "low",
                "tou_winter": False,
            },
        )

    assert result["step_id"] == "tariff_rates"
    assert _fields(result) == {"tou_rate_low", "tou_rate_standard"}


async def test_overlapping_windows_are_sent_back(hass) -> None:
    """An hour listed as both cheap and expensive is a misread price sheet."""
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TARIFF_INPUT
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**DANISH_WINDOWS, "tou_low_end": 18},
        )

    assert result["step_id"] == "tariff_windows"
    assert result["errors"]["base"] == "tou_windows_overlap"


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        # The estimate has a grid fee of its own; a second would double it.
        ({"retail_source": "estimate", "postal_code": "10115"}, "tou_needs_formula"),
        ({"retail_source": "off"}, "tou_needs_formula"),
        # Looking the tariff up only works where it is published.
        ({"tou_source": "datahub"}, "tou_not_supported"),
    ],
)
async def test_a_tariff_needs_the_formula_and_the_right_market(
    hass, changes, expected_error
) -> None:
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ) as mock_validate:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**TARIFF_INPUT, **changes}
        )

    assert result["step_id"] == "user"
    assert result["errors"]["base"] == expected_error
    mock_validate.assert_not_called()


async def test_denmark_looks_the_tariff_up(hass) -> None:
    radius = DatahubTariff(
        gln="5790000705689", owner="Radius Elnet A/S", code="DT_C_01", note="Nettarif C"
    )
    with (
        patch(
            "custom_components.energypriceforecast.config_flow._validate_input",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.energypriceforecast.config_flow"
            ".async_list_household_tariffs",
            new=AsyncMock(return_value=[radius]),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**TARIFF_INPUT, "market": "DK1", "tou_source": "datahub"},
        )
        assert result["step_id"] == "tariff_lookup"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"tou_tariff": radius.value}
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["tou_tariff"] == "5790000705689|DT_C_01"
    # A looked-up tariff carries none of the typed-in fields.
    assert "tou_rate_low" not in result["data"]


async def test_denmark_says_when_the_list_cannot_be_fetched(hass) -> None:
    with (
        patch(
            "custom_components.energypriceforecast.config_flow._validate_input",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.energypriceforecast.config_flow"
            ".async_list_household_tariffs",
            new=AsyncMock(side_effect=DatahubError("429")),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {**TARIFF_INPUT, "market": "DK1", "tou_source": "datahub"},
        )

    assert result["step_id"] == "tariff_lookup"
    assert result["errors"]["base"] == "datahub_unavailable"


ELVIA = """## Elvia AS
GLN: 7080005046220 | Sist oppdatert: 2026-06-05
Kundegrupper: husholdning, fritid
Energiledd grunnpris: 16.99 øre/kWh (eks. avgifter)
Energiledd unntak:
  - Virkedag: 28.99 øre/kWh | timer 6-21 | dager: virkedag
"""


async def test_norway_starts_from_the_grid_operators_collected_tariff(hass) -> None:
    """The fields arrive filled in, and the user still sees and confirms them."""
    with (
        patch(
            "custom_components.energypriceforecast.config_flow._validate_input",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.energypriceforecast.config_flow.async_load_operators",
            new=AsyncMock(return_value=parse_snapshot(ELVIA)),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**TARIFF_INPUT, "market": "NO1"}
        )
        assert result["step_id"] == "tariff_prefill"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"tou_operator": "7080005046220"}
        )
        assert result["step_id"] == "tariff_windows"
        windows = result["data_schema"]({})
        assert (windows["tou_low_start"], windows["tou_low_end"]) == (22, 6)
        assert windows["tou_weekend"] == "low"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], windows
        )
        assert result["step_id"] == "tariff_rates"
        rates = result["data_schema"]({})
        assert rates["tou_rate_low"] == pytest.approx(0.1699)
        assert rates["tou_rate_standard"] == pytest.approx(0.2899)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], rates
        )

    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["tou_rate_standard"] == pytest.approx(0.2899)


async def test_norway_without_a_choice_types_everything_in(hass) -> None:
    with (
        patch(
            "custom_components.energypriceforecast.config_flow._validate_input",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.energypriceforecast.config_flow.async_load_operators",
            new=AsyncMock(return_value=parse_snapshot(ELVIA)),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**TARIFF_INPUT, "market": "NO1"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result["step_id"] == "tariff_windows"
    assert result["data_schema"]({})["tou_low_end"] == 0


async def test_switching_the_tariff_off_drops_its_fields(hass) -> None:
    """An entry must not carry amounts from a tariff it no longer uses."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id="DE",
        data={
            **TARIFF_INPUT,
            "horizon_hours": 48,
            **DANISH_WINDOWS,
            "tou_rate_low": 0.04,
            "tou_rate_standard": 0.06,
            "tou_rate_peak": 0.17,
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.energypriceforecast.config_flow._validate_input",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.energypriceforecast.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**TARIFF_INPUT, "tou_source": "off"}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data["tou_source"] == "off"
    assert not any(key.startswith("tou_") and key != "tou_source" for key in entry.data)


async def test_reconfiguring_starts_from_the_stored_tariff(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=3,
        unique_id="DE",
        data={
            **TARIFF_INPUT,
            "horizon_hours": 48,
            **DANISH_WINDOWS,
            "tou_rate_low": 0.04,
            "tou_rate_standard": 0.06,
            "tou_rate_peak": 0.17,
        },
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.energypriceforecast.config_flow._validate_input",
        new=AsyncMock(return_value=None),
    ):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], TARIFF_INPUT
        )

    assert result["step_id"] == "tariff_windows"
    windows = result["data_schema"]({})
    assert (windows["tou_peak_start"], windows["tou_peak_end"]) == (17, 21)
    assert windows["tou_winter_from"] == 10
