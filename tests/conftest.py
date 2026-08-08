"""Test fixtures."""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/ discoverable by Home Assistant's test harness.

    Without this, config_entries.flow.async_init(DOMAIN, ...) fails with
    homeassistant.data_entry_flow.UnknownHandler because the integration
    was never loaded into the test instance's component registry.
    """
    yield
