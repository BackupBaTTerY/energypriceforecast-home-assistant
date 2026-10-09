"""Keep the README's entity list in step with the entities that exist.

Adding an entity and forgetting to document it is silent: nothing fails, the
integration works, and users only find the entity by scrolling the device
page. The README is the one place people look before installing, so an entity
missing from it is a real gap - this test makes that gap fail loudly.

Only JSON and Markdown are read, so this runs without the
pytest-homeassistant-custom-component plugin.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMPONENT_DIR = REPO_ROOT / "custom_components" / "energypriceforecast"


def _readme_text() -> str:
    """The README with all whitespace runs collapsed.

    Names are wrapped across lines in prose, so a raw substring search would
    miss them for reasons that have nothing to do with the documentation
    being wrong.
    """
    return re.sub(r"\s+", " ", (REPO_ROOT / "README.md").read_text(encoding="utf-8"))


def _entity_names() -> list[tuple[str, str, str]]:
    entity = json.loads(
        (COMPONENT_DIR / "translations" / "en.json").read_text(encoding="utf-8")
    )["entity"]
    return [
        (platform, key, value["name"])
        for platform, entries in entity.items()
        for key, value in entries.items()
    ]


def test_every_entity_is_listed_in_the_readme() -> None:
    readme = _readme_text()

    missing = [
        f"{platform}.{key} ({name!r})"
        for platform, key, name in _entity_names()
        if name not in readme
    ]

    assert not missing, (
        "these entities exist but are not documented in README.md: "
        + ", ".join(sorted(missing))
    )


# Where the README covers each setup option. Entity names have been guarded by
# the test above for a long time; the options were not, and an option that
# never reaches the README is one nobody can discover without clicking through
# the setup dialog. A key is matched by exact name or by prefix, and the
# section named here has to exist, so renaming a section fails loudly instead
# of leaving a stale pointer behind.
SETTING_SECTIONS = {
    "market": "## Supported markets",
    "horizon_hours": "## Horizon",
    "window_hours": "### Always created",
    "api_key": "## Horizon",
    "price_resolution": "## Hourly or quarter-hourly tariff",
    "local_currency": "## Prices in your own currency",
    "postal_code": "## Retail price: estimate or your own formula",
    "retail_": "## Retail price: estimate or your own formula",
    "tou_": "## Time-of-day grid charges",
    "update_interval_minutes": "## Data updates",
    "cheapest_hours_": "### Heat pump or water heater: N cheapest hours per X-hour block",
    "weekend_hours_count": "### EV charging on weekends",
    "greenest_hours_count": "### Charging on clean power instead of cheap power",
    "expensive_hours_count": "### The hours to stay out of",
}


def _settings() -> list[str]:
    source = (COMPONENT_DIR / "const.py").read_text(encoding="utf-8")
    return [
        key
        for _, key in re.findall(
            r'^CONF_([A-Z0-9_]+): Final = "([a-z0-9_]+)"', source, re.M
        )
    ]


def _section_for(key: str) -> str | None:
    if key in SETTING_SECTIONS:
        return SETTING_SECTIONS[key]
    for prefix, section in SETTING_SECTIONS.items():
        if prefix.endswith("_") and key.startswith(prefix):
            return section
    return None


def test_every_setting_is_covered_by_a_readme_section() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    unlisted = [key for key in _settings() if _section_for(key) is None]
    assert not unlisted, (
        "these setup options have no documented home: " + ", ".join(sorted(unlisted))
    )

    missing_sections = sorted(
        {
            section
            for key in _settings()
            if (section := _section_for(key)) and section not in readme
        }
    )
    assert not missing_sections, (
        "the README no longer has these sections: " + ", ".join(missing_sections)
    )
